"""
AIS vessel + track schemas — Brain 3 output.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class VesselTrackPoint(BaseModel):
    time: datetime
    lon: float
    lat: float
    speed_kn: Optional[float] = None
    heading_deg: Optional[float] = None


class VesselInfo(BaseModel):
    vessel_id: str
    mmsi: str
    name: Optional[str] = None
    vessel_type: Optional[str] = Field(None, description="e.g. tanker, cargo, fishing, tug")
    flag: Optional[str] = None
    length_m: Optional[float] = None
    track: list[VesselTrackPoint] = Field(default_factory=list)


class AisSearchRequest(BaseModel):
    incident_id: str
    origin_lon: float
    origin_lat: float
    release_time_start: datetime
    release_time_end: datetime
    radius_km: float | None = Field(None, description="Spatial filter radius around the origin point (default from settings)")
    time_pad_hours: float | None = Field(None, description="Extra hours padded on each side of the release window (default from settings)")
    source: str = Field("gfw", description="Live Global Fishing Watch AIS source; runtime fallback may be used only if live access fails.")


class AisSearchResponse(BaseModel):
    incident_id: str
    vessels: list[VesselInfo]
    using_fallback_data: bool = Field(False, description="DEPRECATED: use data_mode.")
    data_mode: str | None = Field(None, description="Canonical DataMode (M2+).")
    provider_capabilities: list[str] = Field(default_factory=list)
    unavailable_factors: list[str] = Field(default_factory=list)
    message: Optional[str] = None
