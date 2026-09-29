"""
Drift simulation API router — Brain 2.

POST /api/v1/drift/simulate
    Takes a Brain 1 detection result (centroid + detection time) and runs a
    backward OpenDrift simulation to estimate the probable origin region
    and release time window.
"""

from __future__ import annotations

import logging
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException

from app.errors import RealDataUnavailableError
from app.schemas.origin import DriftRequest, OriginResponse
from app.security import require_api_key
from app.services import drift as drift_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/drift", tags=["drift"])


@router.post("/simulate", response_model=OriginResponse, dependencies=[Depends(require_api_key)])
def simulate_drift(request: DriftRequest) -> OriginResponse:
    try:
        estimate = drift_service.run_backward_drift(
            centroid=(request.centroid_lon, request.centroid_lat),
            detection_time=request.detection_time,
            lookback_hours=request.lookback_hours,
            seed=request.seed,
        )
    except RealDataUnavailableError as exc:
        logger.error("Drift simulation blocked — real data unavailable")
        raise HTTPException(status_code=424, detail={"type": "real-data-unavailable", "title": "Real data unavailable", "detail": "Required real environmental data unavailable."}) from exc
    except drift_service.DriftSimulationError as exc:
        # Diagnosable, already-explained failure — surface the real cause
        # to the caller instead of a generic 500.
        logger.error("Drift simulation diagnosed failure")
        raise HTTPException(status_code=422, detail={"type": "drift-failed", "title": "Drift simulation failed", "detail": "Drift simulation failed."}) from exc
    except Exception:  # noqa: BLE001
        logger.exception("Drift simulation failed unexpectedly")
        raise HTTPException(status_code=500, detail={"type": "drift-failed", "title": "Drift simulation failed", "detail": "Drift simulation failed."}) from None

    return OriginResponse(
        incident_id=request.incident_id,
        origin_centroid=estimate.origin_centroid,
        origin=list(estimate.origin_centroid),
        uncertainty_radius_km=estimate.uncertainty_radius_km,
        release_time_window_start=estimate.release_time_window[0],
        release_time_window_end=estimate.release_time_window[1],
        release_window={
            "start": estimate.release_time_window[0].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": estimate.release_time_window[1].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        particle_count=estimate.particle_count,
        final_particle_positions=estimate.final_particle_positions,
        particle_trajectories=estimate.particle_trajectories,
        using_synthetic_environment=estimate.using_synthetic_environment,
        seed=estimate.seed,
        origin_probability=estimate.origin_probability,
        uncertainty_ellipse=estimate.uncertainty_ellipse,
        quality_score=estimate.quality_score,
        confidence=estimate.confidence,
        warnings=list(estimate.warnings or []),
        env_mode=estimate.env_mode,
        release_window_method=estimate.release_window_method,
        message=(
            "LIVE environment unavailable — runtime synthetic ocean/wind fields used. "
            "Set CMEMS_USERNAME/PASSWORD and CDSAPI_KEY in .env for real data."
            if estimate.using_synthetic_environment
            else "Backward drift simulation using real CMEMS + ERA5 data."
        ),
    )
