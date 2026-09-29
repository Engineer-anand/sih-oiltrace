"""
Explainability layer schemas — Brain 6 output. Every candidate vessel gets a
human-readable WHY, not just a number.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    factor: str
    description: str
    log_type: str  # "positive" | "negative" | "info"
    ref: str | None = Field(None, description="Stable evidence reference {vessel_id}:{factor}")


class VesselEvidence(BaseModel):
    vessel_id: str
    mmsi: str
    name: str | None = None
    total_score: float
    checklist: list[EvidenceItem]


class EvidenceResponse(BaseModel):
    incident_id: str
    vessels: list[VesselEvidence]
