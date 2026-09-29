"""
Scoring API router — Brain 4.

POST /api/v1/scores/compute
    Scores a list of candidate vessels (Brain 3 output) against the drift
    origin (Brain 2 output) using the 6-factor deterministic weighted
    formula. Stateless — for the persisted, DB-backed pipeline see
    api/pipeline.py's /api/v1/pipeline/run.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas.ais import VesselInfo
from app.schemas.scoring import ScoringResponse
from app.services.scoring_service import score_vessels

router = APIRouter(prefix="/api/v1/scores", tags=["scoring"])


class ScoreComputeRequest(BaseModel):
    incident_id: str
    origin_lon: float
    origin_lat: float
    release_time_start: datetime
    release_time_end: datetime
    drift_particles: list[tuple[float, float]] = Field(default_factory=list)
    vessels: list[VesselInfo]
    radius_km: float = 20.0
    capabilities: list[str] = Field(default_factory=lambda: ["AIS_PRESENCE"])
    trajectories: list[list[dict]] = Field(default_factory=list)


@router.post("/compute", response_model=ScoringResponse)
def compute_scores(request: ScoreComputeRequest) -> ScoringResponse:
    scores = score_vessels(
        vessels=request.vessels,
        origin_lon=request.origin_lon,
        origin_lat=request.origin_lat,
        window_start=request.release_time_start,
        window_end=request.release_time_end,
        drift_particles=request.drift_particles,
        radius_km=request.radius_km,
        capabilities=request.capabilities,
        trajectories=request.trajectories,
    )
    return ScoringResponse(incident_id=request.incident_id, vessels=scores)
