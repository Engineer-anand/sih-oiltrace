"""
Detection API router — Brain 1.

POST /api/v1/detect
    Accepts a SAR image upload (GeoTIFF preferred; PNG/JPG accepted for
    PNG/JPG images when a real manual bounding box is supplied), runs the
    segmentation model, extracts spill geometry, and returns the
    standardized Incident JSON.
"""

from __future__ import annotations

import asyncio
import logging
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from app.config import settings
from app.contracts import to_iso_z
from app.errors import RealDataUnavailableError
from app.security import require_api_key, validate_incident_id, validate_raster_url
from fastapi import Depends
from app.schemas.incident import DetectionInfo, IncidentResponse, SlickInfo, ModelDiagnostics, RasterPreview
from app.services import geospatial
from app.services.inference import get_detector
from skimage.measure import label

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["detection"])

MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_BYTES


def _save_diagnostics(incident_id: str, filename: str, p, base, meta: dict):
    import numpy as np
    from PIL import Image
    out = settings.UPLOADS_DIR / incident_id
    out.mkdir(parents=True, exist_ok=True)
    if p is None or base is None:
        return
    Image.fromarray((np.clip(p, 0, 1) * 255).astype(np.uint8)).save(out / 'probability.png')
    m = (p > meta.get('threshold', 0.5)).astype(np.uint8) * 255
    Image.fromarray(m).save(out / 'mask.png')
    base_u8 = (np.clip(base, 0, 1) * 255).astype(np.uint8)
    ov = np.stack([base_u8, base_u8, base_u8], axis=-1)
    pos = m > 0
    ov[pos, 0] = 255
    ov[pos, 1] = (ov[pos, 1] * 0.25).astype(np.uint8)
    ov[pos, 2] = (ov[pos, 2] * 0.25).astype(np.uint8)
    Image.fromarray(ov).save(out / 'overlay.png')
    Image.fromarray(base_u8).save(out / 'normalized.png')
    (out / 'metadata.json').write_text(json.dumps(meta, indent=2))


@router.post("/detect", response_model=IncidentResponse, dependencies=[Depends(require_api_key)])
async def detect_oil_spill(
    background_tasks: BackgroundTasks = None,
    file: UploadFile | None = File(None, description="SAR image: GeoTIFF (.tif) preferred, PNG/JPG accepted."),
    min_lon: Optional[float] = Form(
        None, description="Required only if the upload has no embedded georeferencing."
    ),
    min_lat: Optional[float] = Form(None),
    max_lon: Optional[float] = Form(None),
    max_lat: Optional[float] = Form(None),
    detected_at: Optional[datetime] = Form(
        None, description="UTC capture time. Defaults to now() if omitted — set this for real imagery."
    ),
    internal_raster_url: Optional[str] = None,
) -> IncidentResponse:
    incident_id = f"OS-{uuid.uuid4().hex[:8].upper()}"

    # Stream large uploads to disk. This avoids a second full-size in-memory copy
    # for real Sentinel-1 GRD scenes. For AWS COG assets, prefer direct HTTP
    # range/window reads so the full 200+ MB scene never needs to be downloaded.
    upload_path = None
    try:
        if internal_raster_url:
            validate_raster_url(internal_raster_url)
            if None in (min_lon, min_lat, max_lon, max_lat):
                raise HTTPException(status_code=422, detail={"type": "missing-aoi", "title": "AOI required", "detail": "AWS Sentinel-1 remote raster processing requires a real AOI bbox."})
            filename = "sentinel1_remote.tif"
            array, transform, crs = geospatial.load_remote_raster_window(
                internal_raster_url,
                (min_lon, min_lat, max_lon, max_lat),
                max_pixels=settings.INFERENCE_MAX_PIXELS,
            )
        else:
            if file is None:
                raise HTTPException(status_code=400, detail={"type": "missing-input", "title": "Missing input", "detail": "Provide a local file or remote Sentinel-1 raster URL."})
            filename = file.filename or "upload.bin"
        lower = filename.lower()
        if lower.endswith((".tif", ".tiff")):
            import tempfile, os
            suffix = ".tif" if lower.endswith(".tif") else ".tiff"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(settings.DATA_DIR)) as tmp:
                total = 0
                while True:
                    chunk = await file.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail={"type": "upload-too-large", "title": "Upload too large", "detail": "Uploaded file exceeds the configured limit."})
                    tmp.write(chunk)
                upload_path = tmp.name
            if total == 0:
                raise HTTPException(status_code=400, detail={"type": "empty-upload", "title": "Empty upload", "detail": "Uploaded file is empty."})
            array, transform, crs = geospatial.load_image_array_from_path(upload_path,
                bbox_wgs84=(min_lon,min_lat,max_lon,max_lat) if None not in (min_lon,min_lat,max_lon,max_lat) else None,
                max_pixels=settings.INFERENCE_MAX_PIXELS)
        else:
            raw_chunks: list[bytes] = []
            raw_total = 0
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                raw_total += len(chunk)
                if raw_total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail={"type": "upload-too-large", "title": "Upload too large", "detail": "Uploaded file exceeds the configured limit."})
                raw_chunks.append(chunk)
            raw_bytes = b"".join(raw_chunks)
            if len(raw_bytes) == 0:
                raise HTTPException(status_code=400, detail={"type": "empty-upload", "title": "Empty upload", "detail": "Uploaded file is empty."})
            array, transform, crs = geospatial.load_image_array_from_bytes(raw_bytes)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("Failed to decode uploaded image")
        raise HTTPException(status_code=422, detail={"type": "unreadable-image", "title": "Unreadable image", "detail": "Could not read uploaded image."}) from None

    if transform is None or crs is None:
        manual_bbox_fields = [min_lon, min_lat, max_lon, max_lat]
        if any(v is None for v in manual_bbox_fields):
            raise HTTPException(
                status_code=422,
                detail={"type": "missing-georeferencing", "title": "Georeferencing required",
                        "detail": ("Uploaded image has no embedded geotransform (not a GeoTIFF, or "
                                   "missing CRS). Provide min_lon, min_lat, max_lon, max_lat form "
                                   "fields describing the image's real-world extent.")},
            )
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and
                -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise HTTPException(status_code=422, detail={"type": "invalid-bbox", "title": "Invalid bounding box", "detail": "Bounding box coordinates are outside valid lon/lat ranges."})
        if min_lon >= max_lon or min_lat >= max_lat:
            raise HTTPException(status_code=422, detail={"type": "invalid-bbox", "title": "Invalid bounding box", "detail": "Bounding box must satisfy min_lon < max_lon and min_lat < max_lat."})
        height, width = array.shape
        transform, crs = geospatial.synthetic_transform_from_bbox(
            width, height, min_lon, min_lat, max_lon, max_lat
        )

    normalized = array.astype("float32")
    # M-model: single shared preprocessing implementation.
    from app.preprocessing import normalize_minmax

    normalized = normalize_minmax(normalized)

    try:
        detector = get_detector()
    except RealDataUnavailableError as exc:
        raise HTTPException(status_code=424, detail={"type": "model-unavailable", "title": "Model unavailable", "detail": "Trained model is unavailable."}) from exc
    # M4: CPU-bound inference + morphology run in a worker thread so the
    # async event loop stays responsive to /health and other requests.
    # M15: inference latency observed for operations metrics.
    import time as _time

    from app.observability import metrics as _metrics

    def _infer() -> tuple:
        _t0 = _time.perf_counter()
        try:
            m = detector.predict(normalized)
            n = int(label(m, connectivity=1).max())
            return m, n
        finally:
            _metrics.observe("ml_inference_ms", (_time.perf_counter() - _t0) * 1000)

    mask, n_components = await asyncio.to_thread(_infer)
    meta = {
        'incident_id': incident_id,
        'source_filename': filename,
        'model_name': 'trained U-Net',
        'threshold': detector.last_threshold,
        'positive_pixels': detector.last_positive_pixels,
        'positive_percent': detector.last_positive_percent,
        'probability_min': detector.last_probability_min,
        'probability_max': detector.last_probability_max,
        'probability_mean': detector.last_probability_mean,
        'mean_positive_probability': detector.last_mean_confidence,
        'connected_components': n_components,
        'prediction_rejected': detector.last_prediction_rejected,
        'guard_reason': detector.last_guard_reason,
    }
    if background_tasks is not None:
        background_tasks.add_task(_save_diagnostics, incident_id, filename, detector.last_probability, detector.last_model_input, meta)
    else:
        _save_diagnostics(incident_id, filename, detector.last_probability, detector.last_model_input, meta)

    logger.info("DETECTION SUCCESS | incident=%s | model=%s | confidence=%.4f | positive_pixels=%d | positive_percent=%.2f", incident_id, "trained-unet" if not detector.using_dummy else "dummy", detector.last_mean_confidence, detector.last_positive_pixels, detector.last_positive_percent)

    # M4: polygonize + reproject off the event loop (shapely/rasterio CPU work).
    def _vectorize():
        native = geospatial.mask_to_polygons(mask, transform, crs)
        wgs84 = geospatial.reproject_polygons_to_wgs84(native, crs)
        return geospatial.compute_geometry(wgs84)

    geometry = await asyncio.to_thread(_vectorize)

    name_lower = filename.lower()
    # Release audit: never claim a sensor we cannot tie to the input. The AWS
    # COG path is Sentinel-1 by construction; local uploads are only labeled
    # when the filename carries a recognizable mission signature.
    if internal_raster_url:
        satellite_name = "Sentinel-1"
    elif "palsar" in name_lower or "alos" in name_lower:
        satellite_name = "ALOS PALSAR"
    elif any(tok in name_lower for tok in ("sentinel", "s1a", "s1b", "s1_", "grd")):
        satellite_name = "Sentinel-1"
    else:
        satellite_name = "Unspecified sensor"
    detection_info = DetectionInfo(
        satellite=satellite_name,
        source_filename=filename,
        confidence=round(detector.last_mean_confidence, 4),
        using_dummy_model=detector.using_dummy,
    )

    detected_at_iso = to_iso_z((detected_at or datetime.now(timezone.utc)).astimezone(timezone.utc))

    preview = RasterPreview(
        image_url=f"/api/v1/incidents/{incident_id}/assets/normalized.png",
        corners=geospatial.raster_corners_wgs84(array.shape[1], array.shape[0], transform, crs),
    )

    model_diagnostics = ModelDiagnostics(
        model_name="trained U-Net",
        model_version=getattr(detector, "model_version", "unknown"),
        preprocessing_version=getattr(detector, "preprocessing_version", "unknown"),
        input_size=(256, 256),
        threshold=detector.last_threshold,
        positive_pixels=detector.last_positive_pixels,
        positive_percent=round(detector.last_positive_percent, 4),
        probability_min=round(detector.last_probability_min, 6),
        probability_max=round(detector.last_probability_max, 6),
        probability_mean=round(detector.last_probability_mean, 6),
        mean_positive_probability=round(detector.last_mean_confidence, 6),
        connected_components=n_components,
        has_georeferencing=bool(transform is not None and crs is not None),
        crs=str(crs) if crs is not None else None,
    )

    if geometry is None:
        if upload_path:
            import os; os.unlink(upload_path) if os.path.exists(upload_path) else None
        return IncidentResponse(
            incident_id=incident_id,
            spill_detected=False,
            detected_at=detected_at_iso,
            detection=detection_info,
            model=model_diagnostics,
            slick=None,
            raster_preview=preview,
            message="No candidate oil regions passed the minimum-size filter.",
        )

    slick_info = SlickInfo(
        area_km2=geometry.area_km2,
        centroid=geometry.centroid,
        bbox=geometry.bbox,
        geometry=geometry.polygon_geojson,
    )

    if upload_path:
        import os; os.unlink(upload_path) if os.path.exists(upload_path) else None

    return IncidentResponse(
        incident_id=incident_id,
        spill_detected=True,
        detected_at=detected_at_iso,
        detection=detection_info,
        model=model_diagnostics,
        slick=slick_info,
        raster_preview=preview,
        message=(
            "POSSIBLE OIL SLICK — model output, not a confirmed identification."
            if not detector.using_dummy
            else "DUMMY DETECTOR ACTIVE — replace models/checkpoints/unet_oilspill.pt with a trained model."
        ),
    )


@router.get("/incidents/{incident_id}/assets/{asset_name}")
def incident_asset(incident_id: str, asset_name: str):
    from fastapi.responses import FileResponse
    validate_incident_id(incident_id)
    allowed={"normalized.png","probability.png","mask.png","overlay.png","metadata.json"}
    if asset_name not in allowed: raise HTTPException(status_code=400,detail={"type": "invalid-asset", "title": "Unsupported asset", "detail": "Unsupported diagnostic asset."})
    base = (settings.UPLOADS_DIR / incident_id).resolve()
    uploads_root = settings.UPLOADS_DIR.resolve()
    if uploads_root not in base.parents and base != uploads_root:
        raise HTTPException(status_code=400, detail={"type": "invalid-id", "title": "Invalid incident ID", "detail": "Invalid incident path."})
    path = base / asset_name
    if not path.exists(): raise HTTPException(status_code=404,detail={"type": "not-found", "title": "Not found", "detail": "Diagnostic asset not found."})
    return FileResponse(path)
