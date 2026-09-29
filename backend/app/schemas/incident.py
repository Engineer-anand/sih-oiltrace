"""
Standardized Incident JSON schema.

This is the contract every downstream module (drift simulation, AIS
attribution, dashboard) consumes and nothing else, so the frontend never
needs to understand ML, rasterio, or geodesy internals.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field




class ModelDiagnostics(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_name: str
    model_version: str | None = Field(None, description="MODEL_VERSION manifest version, or 'unknown'")
    preprocessing_version: str | None = Field(None, description="Shared preprocessing version (ml/preprocessing.py)")
    input_size: tuple[int, int]
    threshold: float
    positive_pixels: int
    positive_percent: float
    probability_min: float
    probability_max: float
    probability_mean: float
    mean_positive_probability: float
    connected_components: int
    has_georeferencing: bool
    crs: str | None = None

class DetectionInfo(BaseModel):
    satellite: str = Field(..., examples=["Sentinel-1"])
    source_filename: str
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence, not certainty of oil.")
    using_dummy_model: bool = Field(
        False, description="DEPRECATED (M2): always False. Dummy detector removed."
    )


class SlickInfo(BaseModel):
    area_km2: float
    centroid: tuple[float, float] = Field(..., description="(lon, lat) in EPSG:4326")
    bbox: tuple[float, float, float, float] | None = Field(
        None, description="(min_lon, min_lat, max_lon, max_lat) in EPSG:4326"
    )
    geometry: dict[str, Any] = Field(..., description="GeoJSON Polygon or MultiPolygon, EPSG:4326")


class RasterPreview(BaseModel):
    image_url: str
    corners: list[tuple[float, float]]


class IncidentResponse(BaseModel):
    incident_id: str
    spill_detected: bool
    detected_at: Optional[str] = Field(None, description="ISO8601 UTC capture time used downstream")
    detection: DetectionInfo
    model: ModelDiagnostics | None = None
    raster_preview: RasterPreview | None = None
    source_type: str | None = None
    aws_scene_id: str | None = None
    slick: Optional[SlickInfo] = None
    message: Optional[str] = Field(
        default=None, description="Human-readable note, e.g. why no spill was detected."
    )
