"""
Attribution scoring schemas — Brain 4 output.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FactorScore(BaseModel):
    factor: str
    weight: float
    raw_score: float = Field(..., ge=0.0, le=100.0, description="0-100, before weighting")
    weighted_score: float = Field(..., description="raw_score * weight")
    status: str = Field("COMPUTED", description="COMPUTED | UNAVAILABLE | DEGRADED")
    note: str | None = None


class VesselScore(BaseModel):
    vessel_id: str
    mmsi: str
    name: str | None = None
    vessel_type: str | None = None
    total_score: float = Field(..., ge=0.0, le=100.0)
    rank: int
    factors: list[FactorScore]
    unavailable_factors: list[str] = Field(default_factory=list)
    applied_weights: dict[str, float] = Field(default_factory=dict)
    confidence: str = Field("medium", description="high | medium | low — analytical confidence")
    data_quality: str = Field("limited", description="high | reduced | limited — evidence completeness")


class ScoringResponse(BaseModel):
    incident_id: str
    vessels: list[VesselScore]
    message: str | None = None
