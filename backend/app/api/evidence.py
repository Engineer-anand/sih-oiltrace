"""
Evidence / explainability API router — Brain 6.

POST /api/v1/evidence/build
    Turns a Brain 4 scoring result into a human-readable evidence
    checklist per vessel. Stateless — for the persisted pipeline see
    api/pipeline.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.evidence import EvidenceResponse
from app.schemas.scoring import VesselScore
from app.services.evidence_service import build_evidence_for_all
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])


class EvidenceBuildRequest(BaseModel):
    incident_id: str
    vessels: list[VesselScore]


@router.post("/build", response_model=EvidenceResponse)
def build_evidence(request: EvidenceBuildRequest) -> EvidenceResponse:
    evidence = build_evidence_for_all(request.vessels)
    return EvidenceResponse(incident_id=request.incident_id, vessels=evidence)
