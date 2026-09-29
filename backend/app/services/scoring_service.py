"""
Attribution scoring engine — Brain 4.

Deterministic weighted formula (matches the architecture diagram exactly):

    Total Score = Sum(Weight_i * FactorScore_i),  range 0-100

    Factor              Weight   What it measures
    ------------------  ------   -----------------------------------------
    Proximity            25%     Distance from vessel to origin region
    Timing               20%     Vessel presence within the release window
    Trajectory           20%     Vessel path/heading aligns with drift path
    Drift Overlap        15%     Vessel track overlaps the drift ensemble
    Behavior             10%     Loitering / speed drop / abnormal movement
    Vessel Type          10%     Tanker / other high-risk vessel type

No ML/black-box component here by design — every factor is a plain,
auditable function of the AIS + drift data, which is what makes the
Brain 6 explainability layer possible (each score decomposes back into
exactly the inputs that produced it). An optional XGBoost/LightGBM
classifier (per the architecture diagram's "Phase 5, optional") can be
layered on top of these same features later without changing this module's
public interface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings
from app.contracts import grade_attribution, renormalize_weights, scoring_weights
from app.schemas.ais import VesselInfo
from app.schemas.scoring import FactorScore, VesselScore
from app.services.ais_service import haversine_km

# Base risk score (0-100) per vessel type — tuned so tankers and other
# high-risk categories score meaningfully higher than routine traffic.
VESSEL_TYPE_BASE_SCORE = {
    "high-risk tanker": 100.0,
    "tanker": 80.0,
    "cargo": 35.0,
    "tug": 45.0,
    "fishing": 20.0,
    "passenger": 15.0,
    "unknown": 30.0,
}

# A vessel moving slower than this (knots) is a loitering/behavior red flag.
LOITER_SPEED_THRESHOLD_KN = 3.0


def _score_proximity(vessel: VesselInfo, origin_lon: float, origin_lat: float, radius_km: float) -> float:
    """Closer to the origin = higher score. Uses the vessel's closest track point."""
    if not vessel.track:
        return 0.0
    min_dist = min(haversine_km(origin_lon, origin_lat, p.lon, p.lat) for p in vessel.track)
    score = max(0.0, 100.0 * (1.0 - min_dist / max(radius_km, 0.001)))
    return round(score, 2)


def _score_timing(vessel: VesselInfo, window_start: datetime, window_end: datetime) -> float:
    """How centrally the vessel's presence falls within the release window."""
    if not vessel.track:
        return 0.0
    window_mid = window_start + (window_end - window_start) / 2
    half_span = max((window_end - window_start).total_seconds() / 2, 1.0)

    best = 0.0
    for p in vessel.track:
        t = p.time if p.time.tzinfo else p.time.replace(tzinfo=timezone.utc)
        if not (window_start <= t <= window_end):
            continue
        offset = abs((t - window_mid).total_seconds())
        best = max(best, 100.0 * (1.0 - offset / half_span))
    return round(max(best, 0.0), 2)


def _score_trajectory(vessel: VesselInfo, origin_lon: float, origin_lat: float) -> float:
    """
    A vessel whose reported heading points AWAY from the origin (i.e. it
    looks like it just departed the release point) scores higher than one
    passing through on an unrelated course. Uses the earliest in-window
    track point.

    M9: only called when motion evidence is available (capability-gated by
    the caller). Missing heading raises instead of returning a neutral
    constant — neutral constants disguised as scores are forbidden.
    """
    if not vessel.track:
        return 0.0
    p = vessel.track[0]
    if p.heading_deg is None:
        raise ValueError("trajectory requires heading_deg")

    bearing_origin_to_vessel = math.degrees(math.atan2(
        math.sin(math.radians(p.lon - origin_lon)) * math.cos(math.radians(p.lat)),
        math.cos(math.radians(origin_lat)) * math.sin(math.radians(p.lat))
        - math.sin(math.radians(origin_lat)) * math.cos(math.radians(p.lat)) * math.cos(math.radians(p.lon - origin_lon)),
    )) % 360

    angular_diff = abs((p.heading_deg - bearing_origin_to_vessel + 180) % 360 - 180)
    # 0 deg diff (heading matches "pointing away from origin") -> 100; 180 deg (heading back toward origin) -> 0.
    score = 100.0 * (1.0 - angular_diff / 180.0)
    return round(max(0.0, min(100.0, score)), 2)


def _score_drift_overlap_timesliced(
    vessel: VesselInfo,
    trajectories: list[list[dict]],
    window_start: datetime,
    window_end: datetime,
    radius_km: float,
) -> tuple[float, str]:
    """M9: time-sliced drift overlap.

    For each in-window vessel fix, compare against the particle cloud at the
    nearest trajectory time slice; the score is the fraction of fixes within
    the overlap threshold. Returns (score, status).
    """
    from datetime import timezone as _tz

    if not vessel.track or not trajectories:
        return 0.0, "DEGRADED"
    # Flatten trajectory points with parsed times.
    cloud: list[tuple[datetime, float, float]] = []
    for traj in trajectories:
        for p in traj:
            try:
                t = p.get("time")
                tdt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                if tdt.tzinfo is None:
                    tdt = tdt.replace(tzinfo=_tz.utc)
                cloud.append((tdt, float(p["lon"]), float(p["lat"])))
            except Exception:
                continue
    if not cloud:
        return 0.0, "DEGRADED"
    cloud.sort(key=lambda c: c[0])
    threshold_km = max(5.0, radius_km * 0.25)
    hits = 0
    total = 0
    for fix in vessel.track:
        t = fix.time if fix.time.tzinfo else fix.time.replace(tzinfo=_tz.utc)
        if not (window_start <= t <= window_end):
            continue
        total += 1
        best = min(
            abs((ct - t).total_seconds()) for ct, _, _ in cloud
        )
        near = [c for c in cloud if abs((c[0] - t).total_seconds()) - best < 3600]
        dist = min(haversine_km(fix.lon, fix.lat, c[1], c[2]) for c in near)
        if dist <= threshold_km:
            hits += 1
    if total == 0:
        return 0.0, "DEGRADED"
    return round(100.0 * hits / total, 2), "COMPUTED"


def _score_drift_overlap(vessel: VesselInfo, drift_particles: list[tuple[float, float]]) -> float:
    """How close the vessel's track passes to the backward-drift particle cloud."""
    if not vessel.track or not drift_particles:
        return 0.0
    min_dist_km = min(
        haversine_km(p.lon, p.lat, dp_lon, dp_lat)
        for p in vessel.track for dp_lon, dp_lat in drift_particles
    )
    # Within 2km of the ensemble -> full score; falls off linearly to 0 at 20km.
    score = max(0.0, 100.0 * (1.0 - (min_dist_km - 2.0) / 18.0)) if min_dist_km > 2.0 else 100.0
    return round(min(100.0, score), 2)


def _score_behavior(vessel: VesselInfo) -> float:
    """Loitering (sustained low speed) or an abrupt speed drop reads as a
    behavioral red flag consistent with a discharge/release event.

    M9: missing speed data raises instead of returning a neutral constant —
    the caller marks the factor UNAVAILABLE and renormalizes weights.
    """
    speeds = [p.speed_kn for p in vessel.track if p.speed_kn is not None]
    if not speeds:
        raise ValueError("behavior requires speed_kn")
    avg_speed = sum(speeds) / len(speeds)
    if avg_speed <= LOITER_SPEED_THRESHOLD_KN:
        return 100.0
    # Linear falloff: fully "normal" behavior by 12 knots average.
    score = max(0.0, 100.0 * (1.0 - (avg_speed - LOITER_SPEED_THRESHOLD_KN) / 9.0))
    return round(score, 2)


def _score_vessel_type(vessel: VesselInfo) -> float:
    key = (vessel.vessel_type or "unknown").lower()
    return VESSEL_TYPE_BASE_SCORE.get(key, VESSEL_TYPE_BASE_SCORE["unknown"])


def _has_motion(vessels: list[VesselInfo], field: str) -> bool:
    return any(getattr(p, field) is not None for v in vessels for p in v.track)


def score_vessels(
    vessels: list[VesselInfo],
    origin_lon: float, origin_lat: float,
    window_start: datetime, window_end: datetime,
    drift_particles: list[tuple[float, float]],
    radius_km: float = 20.0,
    capabilities: list[str] | None = None,
    trajectories: list[list[dict]] | None = None,
) -> list[VesselScore]:
    """Score every candidate vessel and return them ranked highest-first.

    M9 capability gating: trajectory/behavior are COMPUTED only when the
    provider supplies motion evidence (AIS_TRACK or TRAJECTORY_EVIDENCE).
    Otherwise they are UNAVAILABLE — excluded from the weighted sum with
    weights renormalized over available factors. No neutral constants.
    """
    caps = set(capabilities or ["AIS_PRESENCE"])
    motion_ok = bool(caps & {"AIS_TRACK", "TRAJECTORY_EVIDENCE"})
    trajectories = trajectories or []

    unavailable: list[str] = []
    if not motion_ok:
        unavailable = ["trajectory", "behavior"]
    else:
        # Even with a motion-capable provider, individual vessels may lack data.
        if not _has_motion(vessels, "heading_deg") and "trajectory" not in unavailable:
            unavailable.append("trajectory")
        if not _has_motion(vessels, "speed_kn") and "behavior" not in unavailable:
            unavailable.append("behavior")

    available = [f for f in ("proximity", "timing", "trajectory", "drift_overlap", "behavior", "vessel_type")
                 if f not in unavailable]
    weights = renormalize_weights(available)

    results: list[VesselScore] = []
    for vessel in vessels:
        raw: dict[str, float] = {}
        statuses: dict[str, str] = {}
        notes: dict[str, str] = {}
        raw["proximity"] = _score_proximity(vessel, origin_lon, origin_lat, radius_km)
        statuses["proximity"] = "COMPUTED"
        raw["timing"] = _score_timing(vessel, window_start, window_end)
        statuses["timing"] = "COMPUTED"
        if "trajectory" in unavailable:
            raw["trajectory"] = 0.0
            statuses["trajectory"] = "UNAVAILABLE"
            notes["trajectory"] = "provider supplies presence only; no heading evidence"
        else:
            try:
                raw["trajectory"] = _score_trajectory(vessel, origin_lon, origin_lat)
                statuses["trajectory"] = "COMPUTED"
            except ValueError:
                raw["trajectory"] = 0.0
                statuses["trajectory"] = "UNAVAILABLE"
                notes["trajectory"] = "no heading data for this vessel"
                if "trajectory" not in unavailable:
                    unavailable.append("trajectory")
        if trajectories:
            score, status = _score_drift_overlap_timesliced(
                vessel, trajectories, window_start, window_end, radius_km)
            raw["drift_overlap"] = score
            statuses["drift_overlap"] = status
            if status == "DEGRADED":
                notes["drift_overlap"] = "no time-matched particle cloud; static fallback"
                raw["drift_overlap"] = _score_drift_overlap(vessel, drift_particles)
        else:
            raw["drift_overlap"] = _score_drift_overlap(vessel, drift_particles)
            statuses["drift_overlap"] = "DEGRADED"
            notes["drift_overlap"] = "no trajectory time slices; static proximity to final cloud"
        if "behavior" in unavailable:
            raw["behavior"] = 0.0
            statuses["behavior"] = "UNAVAILABLE"
            notes["behavior"] = "provider supplies presence only; no speed evidence"
        else:
            try:
                raw["behavior"] = _score_behavior(vessel)
                statuses["behavior"] = "COMPUTED"
            except ValueError:
                raw["behavior"] = 0.0
                statuses["behavior"] = "UNAVAILABLE"
                notes["behavior"] = "no speed data for this vessel"
                if "behavior" not in unavailable:
                    unavailable.append("behavior")
        raw["vessel_type"] = _score_vessel_type(vessel)
        statuses["vessel_type"] = "COMPUTED"

        # Renormalize per vessel in case individual vessels lost factors.
        vessel_unavail = [f for f in unavailable if statuses.get(f) == "UNAVAILABLE"]
        vessel_avail = [f for f in raw if f not in vessel_unavail]
        w = renormalize_weights(vessel_avail) if set(vessel_avail) != set(available) else weights

        def _factor(name: str) -> FactorScore:
            # Quantize the weight to its published precision (4 dp) FIRST and
            # derive weighted_score from that same value, so report rendering
            # and API recomputation from the published weight x raw_score
            # reproduce the stored total exactly (no double-rounding drift).
            w_pub = round(w.get(name, 0.0), 4)
            return FactorScore(factor=name,
                               weight=w_pub,
                               raw_score=raw[name],
                               weighted_score=round(w_pub * raw[name], 2),
                               status=statuses.get(name, "COMPUTED"),
                               note=notes.get(name))

        factors = [_factor(name) for name in raw]
        # Release audit: every available factor contributes — DEGRADED factors
        # are real, approximate data and carry their applied weight; only
        # UNAVAILABLE factors are excluded (weights renormalized above).
        # This keeps Σ applied weights == 1.0 the set actually summed and makes
        # report/API recomputation equal the stored total exactly.
        total = round(sum(f.weighted_score for f in factors if f.status != "UNAVAILABLE"), 2)
        n_unavail = len([f for f in factors if f.status == "UNAVAILABLE"])
        n_degraded = len([f for f in factors if f.status == "DEGRADED"])
        confidence, data_quality = grade_attribution(n_unavail, n_degraded)
        results.append(VesselScore(
            vessel_id=vessel.vessel_id, mmsi=vessel.mmsi, name=vessel.name,
            vessel_type=vessel.vessel_type, total_score=total, rank=0, factors=factors,
            unavailable_factors=sorted(set(vessel_unavail)),
            applied_weights={k: round(v, 4) for k, v in w.items()},
            confidence=confidence, data_quality=data_quality,
        ))

    results.sort(key=lambda v: v.total_score, reverse=True)
    for i, v in enumerate(results, start=1):
        v.rank = i
    return results
