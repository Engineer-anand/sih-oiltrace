"""
Standardized Origin JSON schema — Brain 2 output, consumed by Brain 3
(AIS vessel matching).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.config import settings


class DriftRequest(BaseModel):
    """Input to /api/v1/drift/simulate — normally built directly from a
    Milestone 1 IncidentResponse (incident.slick.centroid + detection_time)."""

    incident_id: str
    centroid_lon: float = Field(..., description="Detected slick centroid longitude (EPSG:4326)")
    centroid_lat: float = Field(..., description="Detected slick centroid latitude (EPSG:4326)")
    detection_time: datetime = Field(..., description="UTC time the slick was observed by satellite")
    lookback_hours: int = Field(settings.DEFAULT_LOOKBACK_HOURS, ge=1, le=168, description="How far back to simulate (max 7 days)")
    seed: int | None = Field(None, description="Deterministic seed; defaults to DRIFT_SEED.")


class OriginResponse(BaseModel):
    incident_id: str
    origin_centroid: tuple[float, float] = Field(..., description="(lon, lat) estimated release point")
    # Canonical additive fields (M2): list centroid + object release window.
    origin: list[float] | None = Field(None, description="Canonical [lon, lat] centroid")
    release_window: dict | None = Field(None, description="Canonical {start, end} UTC ISO-8601 Z")
    uncertainty_radius_km: float
    release_time_window_start: datetime
    release_time_window_end: datetime
    particle_count: int
    final_particle_positions: list[tuple[float, float]] = Field(
        default_factory=list, description="Final backward-drift particle cloud, for map overlay/scoring"
    )
    particle_trajectories: list[list[dict]] = Field(
        default_factory=list, description="Sampled particle trajectories with UTC timestamps for backtracking animation"
    )
    using_synthetic_environment: bool = Field(
        ..., description="True if CMEMS/ERA5 credentials weren't configured, or the real download was unusable."
    )
    # M6 additive fields
    seed: int | None = None
    origin_probability: float | None = None
    uncertainty_ellipse: dict | None = None
    quality_score: float | None = None
    confidence: str | None = None
    warnings: list[str] = Field(default_factory=list)
    env_mode: str | None = None
    release_window_method: str | None = None
    message: Optional[str] = None
