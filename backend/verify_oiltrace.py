"""OilTrace preflight check. Run from backend with the project environment active."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
from affine import Affine
from fastapi.testclient import TestClient
from rasterio.crs import CRS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    os.environ.setdefault("ALLOW_LIVE_FALLBACK", "true")
    os.environ.setdefault("REQUIRE_REAL_MODEL", "true")
    from app.main import app
    from app.services.geospatial import compute_geometry, mask_to_polygons, reproject_polygons_to_wgs84
    from app.services.inference import get_detector

    errors: list[str] = []
    checkpoint = ROOT / "models" / "checkpoints" / "unet_oilspill.pt"
    if not checkpoint.exists():
        errors.append(f"Missing checkpoint: {checkpoint}")
    else:
        try:
            detector = get_detector()
            if detector.using_dummy:
                errors.append("Detector is using the dummy fallback.")
            mask = detector.predict(np.zeros((256, 256), dtype=np.float32))
            if mask.shape != (256, 256):
                errors.append(f"Unexpected model mask shape: {mask.shape}")
        except Exception as exc:
            errors.append(f"Model load/inference failed: {exc}")

    # Verify raster -> geometry conversion independently of model output.
    try:
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[8:24, 8:24] = 1
        polygons = mask_to_polygons(mask, Affine.translation(50, 26) * Affine.scale(0.01, -0.01), CRS.from_epsg(4326))
        geometry = compute_geometry(reproject_polygons_to_wgs84(polygons, CRS.from_epsg(4326)))
        if geometry is None or geometry.area_km2 <= 0:
            errors.append("Geospatial mask -> polygon -> area check failed.")
    except Exception as exc:
        errors.append(f"Geospatial check failed: {exc}")

    with TestClient(app) as client:
        r = client.get("/health")
        if r.status_code != 200:
            errors.append(f"/health returned HTTP {r.status_code}")
        routes = {(route.path, tuple(sorted(route.methods or ()))) for route in app.routes if getattr(route, "path", None)}
        paths = {p for p, _ in routes}
        required = {"/api/v1/detect", "/api/v1/drift/simulate", "/api/v1/ais/search", "/api/v1/ais/history", "/api/v1/scores/compute", "/api/v1/evidence/build", "/api/v1/pipeline/run", "/api/v1/pipeline/jobs", "/api/v1/spills", "/api/v1/spills/nearby", "/api/v1/report/{spill_id}", "/api/v1/sentinel1/search", "/api/v1/sentinel1/scene/{scene_id}", "/api/v1/sentinel1/download", "/api/v1/db/status", "/api/v1/health/ready", "/metrics"}
        missing = required - paths
        if missing:
            errors.append(f"Missing routes: {sorted(missing)}")
        # Frontend entry: /app/dashboard when dist is built, else /app fallback.
        if "/app/dashboard" not in paths and "/app" not in paths:
            errors.append("Missing frontend entry route (/app/dashboard or /app).")

    if errors:
        print("OILTRACE PREFLIGHT: FAIL")
        for e in errors:
            print("-", e)
        return 1
    print("OILTRACE PREFLIGHT: PASS")
    print("- trained U-Net checkpoint loads")
    print("- model inference shape check passes")
    print("- geospatial vectorization/area check passes")
    print("- required API + AWS Sentinel-1 routes are registered")
    print("- database status + nearby spatial query routes are registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
