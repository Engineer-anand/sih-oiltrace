"""AIS ingestion service — Brain 3 (M7: provider abstraction).

Pipeline:
    Origin JSON (Brain 2 output: origin centroid + release time window)
        -> provider query (GFW presence | scenario fleet | dev-only fallback)
        -> adapt to VesselInfo/VesselTrackPoint
        -> spatial filter: within `radius_km` of the origin
        -> temporal filter: within the release time window (+/- pad)
        -> candidate vessel list, handed to Brain 4 for scoring

Capability honesty: the GFW source provides AIS_PRESENCE, not tracks.
Speed/heading from live data remain unknown; motion factors are UNAVAILABLE
downstream (see scoring). No static pre-recorded dataset is bundled.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import scenario_allowed, settings, synthetic_fallback_allowed
from app.contracts import AisCapability, to_iso_z
from app.errors import RealDataUnavailableError
from app.providers.ais.base import ProviderResult, ProviderShapeError, RawPosition
from app.providers.ais.gfw_presence import GfwPresenceProvider
from app.providers.ais.scenario import ScenarioFleetProvider
from app.schemas.ais import VesselInfo, VesselTrackPoint

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088

VESSEL_TYPES = ["tanker", "cargo", "fishing", "tug", "passenger", "high-risk tanker"]

# Factors that require motion data no presence-only provider can supply.
PRESENCE_UNAVAILABLE_FACTORS = ["trajectory", "behavior"]


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in km between two (lon, lat) points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class AisFetchResult:
    vessels: list[VesselInfo]
    using_fallback: bool
    mode: str  # LIVE | FALLBACK | SCENARIO (FAILED raises instead)
    capabilities: list[str] = field(default_factory=list)
    unavailable_factors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


def _runtime_fallback_positions(
    origin_lon: float, origin_lat: float,
    window_start: datetime, window_end: datetime,
    n_vessels: int = 8,
) -> list[RawPosition]:
    """Development-only synthetic fallback (M7).

    Honest by construction: non-plausible FALLBACK-NN identifiers, suffixed
    names, uniform random distances (no planted near-origin winner), and the
    caller labels the whole result FALLBACK. Unreachable outside development.
    """
    rng = random.Random(hash((round(origin_lon, 3), round(origin_lat, 3))) & 0xFFFFFFFF)
    rows: list[RawPosition] = []
    window_mid = window_start + (window_end - window_start) / 2
    for i in range(n_vessels):
        mmsi = f"FALLBACK-{i + 1:02d}"
        base_name = rng.choice(["Ocean Star", "Blue Wave", "Sea Glory", "Northern Light", "Pacific Dawn", "Amber Horizon", "Coral Voyager", "Star Fisher"])
        name = f"{base_name} (fallback)"
        vessel_type = rng.choice(VESSEL_TYPES)
        flag = rng.choice(["PA", "LR", "MH", "SG", "IN"])
        length_m = round(rng.uniform(60, 280), 1)
        dist_km = rng.uniform(2.0, 45.0)
        bearing = rng.uniform(0, 360)
        track_time = window_mid + timedelta(hours=rng.uniform(-12, 12))
        speed_kn = rng.uniform(4.0, 18.0)
        brg = math.radians(bearing); d_over_r = dist_km / EARTH_RADIUS_KM; lat1 = math.radians(origin_lat); lon1 = math.radians(origin_lon)
        lat2 = math.asin(math.sin(lat1)*math.cos(d_over_r) + math.cos(lat1)*math.sin(d_over_r)*math.cos(brg))
        lon2 = lon1 + math.atan2(math.sin(brg)*math.sin(d_over_r)*math.cos(lat1), math.cos(d_over_r)-math.sin(lat1)*math.sin(lat2))
        rows.append(RawPosition(mmsi=mmsi, name=name, vessel_type=vessel_type, flag=flag, length_m=length_m, time=track_time.astimezone(timezone.utc), lon=round(math.degrees(lon2),6), lat=round(math.degrees(lat2),6), speed_kn=round(speed_kn,2), heading_deg=round((bearing+rng.uniform(-20,20))%360,1)))
    return rows


def _resolve_radius(radius_km: Optional[float]) -> float:
    return radius_km if radius_km is not None else settings.AIS_SEARCH_RADIUS_KM


def _resolve_pad(time_pad_hours: Optional[float]) -> float:
    return time_pad_hours if time_pad_hours is not None else settings.AIS_TIME_PAD_HOURS


def provider_capabilities(source: str) -> list[str]:
    if source == "scenario":
        return [c.value for c in ScenarioFleetProvider.capabilities]
    return [c.value for c in GfwPresenceProvider.capabilities]


# -- M8: identity, history, track reconstruction ----------------------------

def resolve_identity(mmsi: str, name: str | None = None) -> dict:
    """Normalize a vessel identity (M8).

    Synthetic identifiers (SCENARIO-NN / FALLBACK-NN) resolve to a synthetic
    identity_key and never collide with real MMSIs. Real MMSIs are digit-
    normalized; names are stripped.
    """
    raw = (mmsi or "").strip()
    upper = raw.upper()
    if upper.startswith("SCENARIO-") or upper.startswith("FALLBACK-"):
        return {"identity_key": upper, "mmsi": raw, "synthetic": True,
                "display_name": (name or raw).strip()}
    digits = "".join(c for c in raw if c.isdigit())
    return {"identity_key": digits or upper, "mmsi": digits or raw,
            "synthetic": False, "display_name": (name or digits or raw).strip()}


def persist_positions(positions: list[RawPosition], source: str, quality: str,
                      db=None) -> int:
    """Store fetched positions in the global history with (mmsi, ts, source)
    deduplication (M8). Pass an open Session or leave None for a scoped one.
    Returns the number of newly inserted rows."""
    from app.models.db_models import AisPosition

    if db is None:
        from app.db import session_scope

        with session_scope() as scoped:
            return persist_positions(positions, source, quality, db=scoped)
    inserted = 0
    for pos in positions:
        ident = resolve_identity(pos.mmsi, pos.name)
        ts = pos.time if pos.time.tzinfo else pos.time.replace(tzinfo=timezone.utc)
        exists = db.query(AisPosition).filter(
            AisPosition.mmsi == ident["mmsi"],
            AisPosition.ts == ts,
            AisPosition.source == source,
        ).first()
        if exists:
            continue
        db.add(AisPosition(
            mmsi=ident["mmsi"], identity_key=ident["identity_key"],
            shipname=ident["display_name"], vessel_type=(pos.vessel_type or "unknown").lower(),
            flag=pos.flag or "", length_m=pos.length_m,
            ts=ts, lon=pos.lon, lat=pos.lat,
            speed_kn=pos.speed_kn, heading_deg=pos.heading_deg,
            source=source, quality=quality,
        ))
        inserted += 1
    db.commit()
    return inserted


def persist_vessels(vessels: list[VesselInfo], source: str, quality: str,
                    db=None) -> int:
    """Flatten grouped vessel tracks into raw positions and persist (M8)."""
    raw: list[RawPosition] = []
    for v in vessels:
        for p in v.track:
            raw.append(RawPosition(
                mmsi=v.mmsi, name=v.name or "", vessel_type=v.vessel_type or "unknown",
                flag=v.flag or "", length_m=v.length_m or 0.0,
                time=p.time, lon=p.lon, lat=p.lat,
                speed_kn=p.speed_kn, heading_deg=p.heading_deg,
            ))
    return persist_positions(raw, source, quality, db=db)


def query_history(mmsi: str, start: datetime, end: datetime,
                  include_synthetic: bool = False, db=None) -> list[dict]:
    """Ordered position history for one identity (M8)."""
    from app.models.db_models import AisPosition

    close = False
    if db is None:
        from app.db import session_scope

        db = session_scope().__enter__()
        close = True
    try:
        ident = resolve_identity(mmsi)
        q = db.query(AisPosition).filter(
            AisPosition.identity_key == ident["identity_key"],
            AisPosition.ts >= start, AisPosition.ts <= end,
        )
        if not include_synthetic:
            q = q.filter(AisPosition.quality != "synthetic")
        rows = q.order_by(AisPosition.ts).all()
        return [{"ts": to_iso_z(r.ts), "lon": r.lon, "lat": r.lat,
                 "speed_kn": r.speed_kn, "heading_deg": r.heading_deg,
                 "source": r.source, "quality": r.quality} for r in rows]
    finally:
        if close:
            db.close()


def build_tracks(positions: list[dict], gap_hours: float | None = None) -> dict:
    """Reconstruct track segments with gap detection + staleness (M8).

    Input: ordered position dicts (as from query_history). Output segments
    split wherever consecutive fixes are more than gap_hours apart, plus a
    stale flag when the last fix is older than AIS_STALE_HOURS.
    """
    gap = gap_hours if gap_hours is not None else settings.TRACK_GAP_HOURS
    segments: list[list[dict]] = []
    current: list[dict] = []
    prev_ts = None
    for p in positions:
        ts = datetime.fromisoformat(str(p["ts"]).replace("Z", "+00:00"))
        if prev_ts is not None and (ts - prev_ts).total_seconds() > gap * 3600:
            if current:
                segments.append(current)
            current = []
        current.append(p)
        prev_ts = ts
    if current:
        segments.append(current)
    stale = False
    last_seen = None
    if positions:
        last_seen = positions[-1]["ts"]
        last_dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
        stale = (datetime.now(timezone.utc) - last_dt).total_seconds() > settings.AIS_STALE_HOURS * 3600
    return {"segments": segments, "segment_count": len(segments),
            "fix_count": len(positions), "last_seen": last_seen, "stale": stale}


def get_vessels_near_origin(
    origin_lon: float, origin_lat: float,
    release_time_start: datetime, release_time_end: datetime,
    radius_km: Optional[float] = None, time_pad_hours: Optional[float] = None,
    source: str = "gfw",
) -> AisFetchResult:
    """Query the selected AIS provider, then spatial+temporal filter.

    Returns an AisFetchResult carrying mode, capabilities, unavailable
    factors, warnings, and provenance. Required-real-data failures raise
    RealDataUnavailableError; synthetic substitution happens only in
    development (FALLBACK) or sih_demo (SCENARIO).
    """
    radius_km = _resolve_radius(radius_km)
    time_pad_hours = _resolve_pad(time_pad_hours)
    pad = timedelta(hours=time_pad_hours)
    window_start, window_end = release_time_start - pad, release_time_end + pad
    deg_pad = radius_km / 111.0

    warnings: list[str] = []
    if source == "scenario":
        if not scenario_allowed():
            raise RealDataUnavailableError(
                "Scenario AIS requires APP_MODE=sih_demo. Use source='gfw' for real data."
            )
        result = ScenarioFleetProvider().search(
            origin_lon - deg_pad, origin_lat - deg_pad, origin_lon + deg_pad, origin_lat + deg_pad,
            window_start, window_end,
        )
        using_fallback = True
    elif source != "gfw":
        raise RealDataUnavailableError("AIS source must be 'gfw' or 'scenario'.")
    else:
        try:
            result = GfwPresenceProvider().search(
                origin_lon - deg_pad, origin_lat - deg_pad, origin_lon + deg_pad, origin_lat + deg_pad,
                window_start, window_end,
            )
            logger.info("GFW API SUCCESS | returned_positions=%d", len(result.positions))
            using_fallback = False
        except (RealDataUnavailableError, ProviderShapeError) as exc:
            logger.warning("AIS LIVE UNAVAILABLE | %s", exc)
            if not synthetic_fallback_allowed() and not scenario_allowed():
                raise RealDataUnavailableError(
                    f"GFW live AIS unavailable and fallback substitution is forbidden "
                    f"in APP_MODE={settings.APP_MODE}."
                ) from exc
            warnings.append(f"Live AIS unavailable ({type(exc).__name__}); local fleet records used.")
            logger.warning("FALLBACK START | AIS | reason=live_gfw_unavailable")
            from app.observability import metrics as _metrics

            _metrics.incr("synthetic_fallback_total")
            _metrics.incr("provider_failure_GFW")
            now = datetime.now(timezone.utc)
            iso = now.isoformat().replace("+00:00", "Z")
            result = ProviderResult(
                positions=_runtime_fallback_positions(origin_lon, origin_lat, window_start, window_end),
                mode="FALLBACK", warnings=list(warnings),
                provenance={"provider": "fallback", "dataset": "runtime-synthetic",
                            "request_time": iso, "response_time": iso, "data_time": iso,
                            "age_seconds": 0.0, "cache_hit": False, "mode": "FALLBACK"},
            )
            using_fallback = True
            logger.warning("FALLBACK SUCCESS | AIS | source=runtime_synthetic | in_memory=true")

    def _tz_aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    window_start, window_end = _tz_aware(window_start), _tz_aware(window_end)
    matched: dict[str, VesselInfo] = {}
    for pos in result.positions:
        pos_time = _tz_aware(pos.time)
        if not (window_start <= pos_time <= window_end):
            continue
        dist_km = haversine_km(origin_lon, origin_lat, pos.lon, pos.lat)
        if dist_km > radius_km:
            continue
        vessel = matched.setdefault(pos.mmsi, VesselInfo(
            vessel_id=f"V-{pos.mmsi}", mmsi=pos.mmsi, name=pos.name,
            vessel_type=pos.vessel_type, flag=pos.flag, length_m=pos.length_m, track=[],
        ))
        vessel.track.append(VesselTrackPoint(time=pos_time, lon=pos.lon, lat=pos.lat, speed_kn=pos.speed_kn, heading_deg=pos.heading_deg))
    for vessel in matched.values():
        vessel.track.sort(key=lambda p: p.time)
    logger.info("AIS MATCH SUCCESS | candidates=%d | mode=%s", len(matched), result.mode)
    caps = [c.value for c in (ScenarioFleetProvider.capabilities if source == "scenario" else GfwPresenceProvider.capabilities)]
    unavailable = list(PRESENCE_UNAVAILABLE_FACTORS) if caps == [AisCapability.AIS_PRESENCE.value] else []
    return AisFetchResult(
        vessels=list(matched.values()),
        using_fallback=using_fallback,
        mode=result.mode,
        capabilities=caps,
        unavailable_factors=unavailable,
        warnings=list(warnings) + list(result.warnings),
        provenance=result.provenance,
    )
