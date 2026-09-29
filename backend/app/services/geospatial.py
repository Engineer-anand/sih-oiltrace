"""
Geospatial processing service.

Handles the "Processing" stage of Milestone 1:
    binary mask (pixel space)
        -> vector polygons (raster CRS)
        -> reprojected polygons (EPSG:4326 / WGS84 lat-lon)
        -> geometry summary (bbox, area_km2, centroid)

Two input paths are supported:

1. Georeferenced GeoTIFF (has an affine transform + CRS baked in via
   rasterio). This is the real-world Sentinel-1 GRD product case — pixel
   coordinates carry a genuine ground location.
2. Plain image (PNG/JPG with no embedded geotransform) — e.g. a non-georeferenced image
   or a benchmark-dataset tile. In this case there is no ground truth
   geolocation, so the caller must supply a manual bounding box
   (four corner lat/lons) that anchors the image to a real-world extent.
   This keeps the API honest: we never invent coordinates for an image
   that carries none.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS, Geod, Transformer
from rasterio.features import shapes as rasterio_shapes
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from rasterio.io import MemoryFile
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import unary_union, transform as shapely_transform
from shapely.validation import make_valid

logger = logging.getLogger(__name__)

WGS84 = CRS.from_epsg(4326)
_GEOD = Geod(ellps="WGS84")

# Discard connected components smaller than this many pixels — filters
# speckle noise in the raw mask before it ever becomes a "detected slick".
MIN_COMPONENT_PIXELS = 25


@dataclass
class SpillGeometry:
    polygon_geojson: dict          # GeoJSON geometry (Polygon or MultiPolygon), EPSG:4326
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    area_km2: float
    centroid: tuple[float, float]  # (lon, lat)


def read_geotiff(path: str) -> tuple[np.ndarray, Affine, CRS]:
    """
    Read a single-band GeoTIFF and return (array, affine_transform, crs).

    The affine transform maps pixel (col, row) -> (x, y) in the raster's
    native CRS: x = a*col + b*row + c ; y = d*col + e*row + f.
    rasterio exposes this directly as `dataset.transform`.
    """
    with rasterio.open(path) as ds:
        array = ds.read(1).astype(np.float32)
        transform = ds.transform
        crs = ds.crs
    if crs is None:
        raise ValueError(
            f"{path} has no embedded CRS/geotransform. Use "
            "`synthetic_transform_from_bbox()` if this is a non-georeferenced "
            "test image and you have a known ground-truth bounding box."
        )
    return array, transform, crs


def synthetic_transform_from_bbox(
    width: int,
    height: int,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> tuple[Affine, CRS]:
    """
    Build an affine transform for a plain (non-georeferenced) image by
    anchoring its four corners to a known real-world lat/lon bounding box.

    Use this ONLY when you know the true ground extent of the image
    (e.g. a cropped Sentinel-1 quicklook you sourced yourself). Never used
    implicitly — the caller must provide real coordinates.
    """
    transform = rasterio.transform.from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)
    return transform, WGS84


def mask_to_polygons(mask: np.ndarray, transform: Affine, crs: CRS) -> list[Polygon]:
    """
    Vectorize a binary raster mask into polygons, in the raster's native CRS.

    Pixel -> ground coordinates happen automatically here: `rasterio.features.shapes`
    consumes the affine `transform` and emits polygon vertices already in
    ground units (degrees if CRS is geographic, metres if projected) rather
    than raw pixel indices.
    """
    mask_bool = mask > 0
    if not mask_bool.any():
        return []

    # Pre-filter speckle noise on the raster before vectorization.
    # This prevents thousands of single-pixel polygons from overwhelming rasterio_shapes and unary_union.
    # skimage is optional at import time (locked-down envs may block its DLLs).
    try:
        from skimage.morphology import remove_small_objects

        cleaned_bool = remove_small_objects(mask_bool, min_size=MIN_COMPONENT_PIXELS)
    except Exception:
        cleaned_bool = mask_bool

    if not cleaned_bool.any():
        return []

    mask_uint8 = cleaned_bool.astype(np.uint8)
    polygons: list[Polygon] = []

    for geom, value in rasterio_shapes(mask_uint8, mask=cleaned_bool, transform=transform):
        if value != 1:
            continue
        poly = shape(geom)
        if poly.is_empty:
            continue
        # M6: repair invalid/self-intersecting rings at vectorization time.
        if not poly.is_valid:
            try:
                poly = make_valid(poly)
            except Exception:
                continue
            if poly.is_empty or not poly.is_valid:
                continue
        polygons.append(poly)

    if not polygons:
        return []

    # Filter tiny speckle components. Component "size" in pixels is
    # approximated from polygon area / pixel area in ground units.
    px_w = abs(transform.a)
    px_h = abs(transform.e)
    pixel_area = px_w * px_h if px_w and px_h else None

    if pixel_area:
        polygons = [p for p in polygons if (p.area / pixel_area) >= MIN_COMPONENT_PIXELS]

    return polygons


def reproject_polygons_to_wgs84(polygons: list[Polygon], src_crs: CRS) -> list[Polygon]:
    """Reproject a list of polygons from their native CRS into EPSG:4326 (lat/lon)."""
    if src_crs == WGS84:
        return polygons

    transformer = Transformer.from_crs(src_crs, WGS84, always_xy=True)

    def _project(x, y, z=None):
        lon, lat = transformer.transform(x, y)
        return (lon, lat) if z is None else (lon, lat, z)

    return [shapely_transform(_project, p) for p in polygons]


def geodesic_centroid(geom_wgs84) -> tuple[float, float]:
    """M6: representative seed point computed on a local planar projection.

    The old degree-space ``merged.centroid`` is kept only as a fallback; the
    Stage-4 seed must not depend on degree-space arithmetic for metric
    correctness.
    """
    from shapely.geometry import Point

    try:
        min_lon, min_lat, max_lon, max_lat = geom_wgs84.bounds
        clon, clat = (min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0
        # Local azimuthal-equidistant centred on the geometry.
        from pyproj import CRS as _CRS

        local = _CRS.from_proj4(f"+proj=aeqd +lon_0={clon} +lat_0={clat} +datum=WGS84 +units=m +no_defs")
        to_local = Transformer.from_crs(WGS84, local, always_xy=True)
        to_wgs = Transformer.from_crs(local, WGS84, always_xy=True)

        def _f(x, y, z=None):
            px, py = to_local.transform(x, y)
            return (px, py) if z is None else (px, py, z)

        projected = shapely_transform(_f, geom_wgs84)
        rep = projected.representative_point()
        lon, lat = to_wgs.transform(rep.x, rep.y)
        if isinstance(geom_wgs84, Point):
            return (round(float(geom_wgs84.x), 6), round(float(geom_wgs84.y), 6))
        return (round(float(lon), 6), round(float(lat), 6))
    except Exception:
        c = geom_wgs84.centroid
        return (round(float(c.x), 6), round(float(c.y), 6))


def compute_geometry(polygons_wgs84: list[Polygon]) -> SpillGeometry | None:
    """
    Merge detected polygons into one geometry and compute the summary
    fields that go into the Incident JSON: bbox, geodesic area (km²),
    and centroid.
    """
    if not polygons_wgs84:
        return None

    merged = unary_union(polygons_wgs84)
    if merged.is_empty:
        return None

    if isinstance(merged, Polygon):
        merged_multi = MultiPolygon([merged])
    else:
        merged_multi = merged

    # Geodesic area accounts for the Earth's curvature — far more accurate
    # than naively squaring degree-based bounding boxes, especially at
    # higher latitudes where a degree of longitude covers less ground.
    area_m2 = 0.0
    for poly in merged_multi.geoms:
        poly_area_m2, _ = _GEOD.geometry_area_perimeter(poly)
        area_m2 += abs(poly_area_m2)
    area_km2 = area_m2 / 1_000_000.0

    min_lon, min_lat, max_lon, max_lat = merged.bounds
    centroid_lon, centroid_lat = geodesic_centroid(merged)

    return SpillGeometry(
        polygon_geojson=mapping(merged),
        bbox=(min_lon, min_lat, max_lon, max_lat),
        area_km2=round(area_km2, 4),
        centroid=(centroid_lon, centroid_lat),
    )


def load_remote_raster_window(
    url: str,
    bbox_wgs84: tuple[float, float, float, float],
    max_pixels: int = 2_000_000,
) -> tuple[np.ndarray, Affine, CRS]:
    """Read only a WGS84 AOI from a public Cloud-Optimized GeoTIFF over HTTPS.

    Rasterio/GDAL uses HTTP range requests through /vsicurl/, so OilTrace does
    not need to download a 200+ MB Sentinel-1 scene just to analyze a small AOI.
    Host must pass the SSRF allowlist (see app.security.validate_raster_url).
    """
    from app.security import validate_raster_url
    validate_raster_url(url)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open("/vsicurl/" + url) as ds:
            if ds.crs is None or ds.transform == Affine.identity():
                raise ValueError("Remote Sentinel-1 asset has no CRS/geotransform")
            left, bottom, right, top = bbox_wgs84
            rb = transform_bounds(WGS84, ds.crs, left, bottom, right, top, densify_pts=21)
            window = from_bounds(*rb, transform=ds.transform)
            window = window.intersection(__import__('rasterio').windows.Window(0, 0, ds.width, ds.height))
            width=max(1,int(round(window.width))); height=max(1,int(round(window.height)))
            pixels=width*height
            if pixels>max_pixels:
                scale=(max_pixels/pixels)**0.5
                out_w=max(64,int(width*scale)); out_h=max(64,int(height*scale))
                data=ds.read(1,window=window,out_shape=(out_h,out_w),resampling=Resampling.bilinear).astype(np.float32)
                transform=ds.window_transform(window)*Affine.scale(width/out_w,height/out_h)
            else:
                data=ds.read(1,window=window).astype(np.float32)
                transform=ds.window_transform(window)
            return data, transform, ds.crs

def load_image_array_from_path(
    path: str | Path,
    bbox_wgs84: tuple[float, float, float, float] | None = None,
    max_pixels: int = 2_000_000,
) -> tuple[np.ndarray, Affine | None, CRS | None]:
    """Read a raster from disk, optionally clipping to a WGS84 AOI and downsampling.

    This is the fast path for large Sentinel-1 GRD files: only the requested
    AOI is read into memory instead of loading the whole scene.
    """
    with rasterio.open(path) as ds:
        src_crs = ds.crs
        if ds.crs is None or ds.transform == Affine.identity():
            if bbox_wgs84 is None:
                raise ValueError(f"{path} has no embedded CRS/geotransform")
            width, height = ds.width, ds.height
            pixels = width * height
            if pixels > max_pixels:
                scale = (max_pixels / pixels) ** 0.5
                out_w = max(64, int(width * scale))
                out_h = max(64, int(height * scale))
                data = ds.read(1, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype(np.float32)
            else:
                data = ds.read(1).astype(np.float32)
            # The caller will anchor this pixel array to the supplied real AOI.
            return data, None, None

        window = None
        if bbox_wgs84 is not None:
            left, bottom, right, top = bbox_wgs84
            rb = transform_bounds(WGS84, ds.crs, left, bottom, right, top, densify_pts=21)
            window = from_bounds(*rb, transform=ds.transform)
            window = window.intersection(__import__('rasterio').windows.Window(0, 0, ds.width, ds.height))

        if window is None:
            width, height = ds.width, ds.height
            read_window = None
            base_transform = ds.transform
        else:
            width, height = max(1, int(round(window.width))), max(1, int(round(window.height)))
            read_window = window
            base_transform = ds.window_transform(window)

        pixels = width * height
        if pixels > max_pixels:
            scale = (max_pixels / pixels) ** 0.5
            out_w = max(64, int(width * scale))
            out_h = max(64, int(height * scale))
            data = ds.read(1, window=read_window, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype(np.float32)
            if read_window is None:
                transform = ds.transform * Affine.scale(ds.width / out_w, ds.height / out_h)
            else:
                transform = base_transform * Affine.scale(width / out_w, height / out_h)
        else:
            data = ds.read(1, window=read_window).astype(np.float32)
            transform = base_transform

    return data, transform, src_crs

def load_image_array_from_bytes(data: bytes) -> tuple[np.ndarray, Affine | None, CRS | None]:
    """
    Attempt to read uploaded bytes as a GeoTIFF via an in-memory rasterio
    dataset. Returns (band_1_array, transform, crs); transform/crs are
    None if the file has no embedded georeferencing (e.g. a plain PNG/JPG),
    in which case the caller must supply a bounding box
    via `synthetic_transform_from_bbox`.
    """
    try:
        with MemoryFile(data) as memfile:
            with memfile.open() as ds:
                array = ds.read(1).astype(np.float32)
                transform = ds.transform
                crs = ds.crs
                if crs is None or transform == Affine.identity():
                    return array, None, None
                return array, transform, crs
    except rasterio.errors.RasterioIOError as exc:
        logger.warning("rasterio could not open upload as GeoTIFF (%s); falling back to PIL.", exc)
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data)).convert("L")
        return np.array(img, dtype=np.float32), None, None


def raster_corners_wgs84(width: int, height: int, transform: Affine, crs: CRS) -> list[tuple[float, float]]:
    """Return image corners as WGS84 lon/lat in TL, TR, BR, BL order."""
    pts_native=[transform*(0,0), transform*(width,0), transform*(width,height), transform*(0,height)]
    if crs == WGS84:
        pts=pts_native
    else:
        tr=Transformer.from_crs(crs,WGS84,always_xy=True)
        pts=[tr.transform(x,y) for x,y in pts_native]
    return [(round(float(x),6),round(float(y),6)) for x,y in pts]
