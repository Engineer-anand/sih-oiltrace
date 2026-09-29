"""
AIS API router — Brain 3.

POST /api/v1/ais/search
    Takes a Brain 2 origin result (origin centroid + release time window)
    and returns every vessel with AIS positions inside the spatial +
    temporal search window.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.errors import RealDataUnavailableError
from app.schemas.ais import AisSearchRequest, AisSearchResponse
from app.security import require_api_key
from app.services import ais_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ais", tags=["ais"])


@router.post("/search", response_model=AisSearchResponse, dependencies=[Depends(require_api_key)])
def search_vessels(request: AisSearchRequest) -> AisSearchResponse:
    try:
        fetched = ais_service.get_vessels_near_origin(
            origin_lon=request.origin_lon,
            origin_lat=request.origin_lat,
            release_time_start=request.release_time_start,
            release_time_end=request.release_time_end,
            radius_km=request.radius_km,
            time_pad_hours=request.time_pad_hours,
            source=request.source,
        )
    except RealDataUnavailableError as exc:
        logger.error("AIS search blocked — real data unavailable")
        raise HTTPException(status_code=424, detail={"type": "real-data-unavailable", "title": "Real data unavailable", "detail": "Required real AIS data unavailable."}) from exc
    except Exception:  # noqa: BLE001
        logger.exception("AIS search failed")
        raise HTTPException(status_code=500, detail={"type": "ais-search-failed", "title": "AIS search failed", "detail": "AIS search failed."}) from None

    return AisSearchResponse(
        incident_id=request.incident_id,
        vessels=fetched.vessels,
        using_fallback_data=fetched.using_fallback,
        data_mode=fetched.mode,
        provider_capabilities=fetched.capabilities,
        unavailable_factors=fetched.unavailable_factors,
        message=(
            "Scenario AIS fleet generated around the origin point. Select source='gfw' for live data."
            if fetched.mode == "SCENARIO"
            else ("Development fallback vessels used; not operational data."
                  if fetched.mode == "FALLBACK"
                  else "Vessels from the Global Fishing Watch API.")
        ),
    )


@router.get("/history")
def vessel_history(
    mmsi: str = Query(..., description="MMSI or synthetic identity (SCENARIO-NN)"),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    include_synthetic: bool = Query(False),
) -> dict:
    """M8: ordered position history + reconstructed segments for one identity.

    Synthetic positions are excluded unless include_synthetic=true. Until a
    track-capable provider exists, GFW presence history is presence evidence,
    not trajectory evidence.
    """
    from datetime import timedelta, timezone

    now = datetime.now(timezone.utc)
    start = start or (now - timedelta(days=30))
    end = end or now
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    ident = ais_service.resolve_identity(mmsi)
    # Synthetic identities are explicit demo requests; do not make the
    # tracking page require a hidden query flag to display their history.
    positions = ais_service.query_history(
        mmsi, start, end, include_synthetic=include_synthetic or ident["synthetic"]
    )
    tracks = ais_service.build_tracks(positions)
    freshness = "STALE" if tracks["stale"] else ("RECENT" if positions else "UNAVAILABLE")
    return {"mmsi": ident["mmsi"], "identity_key": ident["identity_key"],
            "synthetic": ident["synthetic"], "positions": positions, **tracks,
            "freshness": freshness,
            "note": "Presence history with periodic refresh; not a live stream." if not ident["synthetic"] else "Synthetic history."}
