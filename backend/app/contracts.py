"""Canonical OilTrace contracts — single source of truth (M2).

All routes/services consume these definitions. Legacy schema fields are kept
as deprecated optionals for compatibility; new canonical fields are additive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

EPSG_CODE = 4326


class DataMode(str, Enum):
    LIVE = "LIVE"
    ARCHIVE = "ARCHIVE"
    FALLBACK = "FALLBACK"
    SCENARIO = "SCENARIO"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class AisCapability(str, Enum):
    AIS_PRESENCE = "AIS_PRESENCE"
    AIS_POSITIONS = "AIS_POSITIONS"
    AIS_TRACK = "AIS_TRACK"
    TRAJECTORY_EVIDENCE = "TRAJECTORY_EVIDENCE"


class FactorStatus(str, Enum):
    COMPUTED = "COMPUTED"
    UNAVAILABLE = "UNAVAILABLE"
    DEGRADED = "DEGRADED"


class StageStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def validate_lonlat(lon: float, lat: float) -> tuple[float, float]:
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError(f"lon/lat out of range: {(lon, lat)}")
    return (lon, lat)


class ReleaseWindow(BaseModel):
    start: datetime = Field(..., description="UTC release window start")
    end: datetime = Field(..., description="UTC release window end")

    @field_validator("start", "end", mode="before")
    @classmethod
    def _coerce(cls, v):
        return parse_utc(v)

    @field_validator("end")
    @classmethod
    def _order(cls, v, info):
        start = info.data.get("start")
        if start and v < start:
            raise ValueError("release window end < start")
        return v

    def to_json(self) -> dict[str, str]:
        return {"start": to_iso_z(self.start), "end": to_iso_z(self.end)}


class ProviderMeta(BaseModel):
    provider: str
    dataset: str | None = None
    request_time: datetime | None = None
    response_time: datetime | None = None
    data_time: datetime | None = None
    age_seconds: float | None = None
    cache_hit: bool = False
    mode: DataMode = DataMode.LIVE

    @field_validator("request_time", "response_time", "data_time", mode="before")
    @classmethod
    def _coerce_dt(cls, v):
        return parse_utc(v) if v is not None else v


class StageResult(BaseModel):
    status: StageStatus
    data_mode: DataMode = DataMode.LIVE
    quality_score: float | None = Field(None, ge=0.0, le=100.0)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def _coerce_dt(cls, v):
        return parse_utc(v) if v is not None else v


class ProblemDetail(BaseModel):
    type: str
    title: str
    status: int
    detail: str | None = None


def problem(status: int, type_: str, title: str, detail: str | None = None) -> dict:
    return {"type": type_, "title": title, "status": status, "detail": detail}


def validate_geojson_geometry(geom: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(geom, dict) or geom.get("type") not in ("Polygon", "MultiPolygon", "Point"):
        raise ValueError("geometry must be GeoJSON Polygon/MultiPolygon/Point")
    coords = geom.get("coordinates")
    if not isinstance(coords, list) or not coords:
        raise ValueError("geometry coordinates missing")
    return geom


CANONICAL_FACTORS = ("proximity", "timing", "trajectory", "drift_overlap", "behavior", "vessel_type")


def scoring_weights() -> dict[str, float]:
    """Single source of truth for attribution weights (settings-backed)."""
    from app.config import settings

    weights = {
        "proximity": float(settings.WEIGHT_PROXIMITY),
        "timing": float(settings.WEIGHT_TIMING),
        "trajectory": float(settings.WEIGHT_TRAJECTORY),
        "drift_overlap": float(settings.WEIGHT_DRIFT_OVERLAP),
        "behavior": float(settings.WEIGHT_BEHAVIOR),
        "vessel_type": float(settings.WEIGHT_VESSEL_TYPE),
    }
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Scoring weights must sum to 1.0, got {total}")
    return weights


def renormalize_weights(available: list[str]) -> dict[str, float]:
    """Renormalize canonical weights over available factors (UNAVAILABLE excluded)."""
    base = scoring_weights()
    sub = {k: base[k] for k in available if k in base}
    total = sum(sub.values())
    if total <= 0:
        raise ValueError("No available factors to renormalize.")
    return {k: v / total for k, v in sub.items()}


def recompute_total(components: dict[str, float], weights: Optional[dict[str, float]] = None) -> float:
    w = weights or scoring_weights()
    return round(sum(components[k] * w[k] for k in components if k in w), 2)


def validate_job_inputs(lookback_hours: int | None, ais_radius_km: float | None) -> None:
    """Pure input-bounds check shared by /pipeline/run and /pipeline/jobs.

    Raises an HTTPException with a typed problem detail on violation so both
    endpoints enforce identical bounds (M4).
    """
    from fastapi import HTTPException

    if lookback_hours is not None and not (1 <= lookback_hours <= 168):
        raise HTTPException(status_code=422, detail={"type": "invalid-lookback", "title": "Invalid lookback", "detail": "lookback_hours must be within 1..168."})
    if ais_radius_km is not None and not (0 < ais_radius_km <= 500):
        raise HTTPException(status_code=422, detail={"type": "invalid-radius", "title": "Invalid radius", "detail": "ais_radius_km must be within (0, 500]."})


_MODE_RANK = {"LIVE": 0, "ARCHIVE": 1, "DEGRADED": 1, "FALLBACK": 2, "SCENARIO": 3, "FAILED": 4}

def worst_mode(*modes: str) -> str:
    """Return the least-live data mode (M7: env mode x AIS mode)."""
    best = "LIVE"
    for m in modes:
        m = (m or "LIVE").upper()
        if _MODE_RANK.get(m, 0) >= _MODE_RANK.get(best, 0):
            best = m
    return best


def format_sse_event(version: int, payload: dict, terminal: bool = False) -> str:
    """M12: SSE framing with id (resume support), data, and terminal event."""
    import json as _json

    body = f"data: {_json.dumps(payload, default=str)}\n\n"
    if terminal:
        return f"id: {version}\nevent: terminal\n{body}"
    return f"id: {version}\n{body}"


def sse_initial_version(last_event_id: str | None) -> int:
    """M12: parse Last-Event-ID resume header; -1 means replay from current."""
    try:
        return int(str(last_event_id).strip())
    except (TypeError, ValueError, AttributeError):
        return -1


def grade_attribution(n_unavailable: int, n_degraded: int) -> tuple[str, str]:
    """M10: (confidence, data_quality) from factor statuses. Single rule used
    by scoring and reporting so both agree."""
    if n_unavailable == 0 and n_degraded == 0:
        return "high", "high"
    if n_unavailable > 2:
        return "low", "limited"
    return "medium", ("reduced" if n_degraded else "limited")


# -- M11: Stage 4 -> Stage 5 boundary ----------------------------------------

STAGE4_REQUIRED = (
    "origin_centroid", "uncertainty_radius_km", "release_time_window_start",
    "release_time_window_end", "particle_count", "using_synthetic_environment",
)


def validate_stage4_output(payload: dict) -> list[str]:
    """Boundary validation for Stage-4 output consumed by Stage 5 (M11).

    Returns a list of problems; empty means the payload is consumable with
    no recomputation or hidden conversion.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    for key in STAGE4_REQUIRED:
        if key not in payload:
            problems.append(f"missing required field: {key}")
    origin = payload.get("origin_centroid", payload.get("origin"))
    try:
        lon, lat = float(origin[0]), float(origin[1])
        validate_lonlat(lon, lat)
    except Exception:
        problems.append("origin must be [lon, lat] within valid ranges")
    window = payload.get("release_window")
    start = payload.get("release_time_window_start")
    end = payload.get("release_time_window_end")
    try:
        if isinstance(window, dict) and "start" in window and "end" in window:
            ReleaseWindow(start=window["start"], end=window["end"])
        elif start and end:
            ReleaseWindow(start=start, end=end)
        else:
            problems.append("release window missing (need release_window{start,end} or start/end pair)")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"invalid release window: {exc}")
    try:
        if int(payload.get("particle_count", 0)) < 0:
            problems.append("particle_count must be >= 0")
    except Exception:
        problems.append("particle_count must be an integer")
    if "data_mode" in payload and str(payload["data_mode"]).upper() not in (
            "LIVE", "ARCHIVE", "FALLBACK", "SCENARIO", "DEGRADED", "FAILED"):
        problems.append("data_mode has an unknown value")
    return problems


def origin_to_ais_request(origin_payload: dict, incident_id: str,
                          radius_km: float | None = None,
                          time_pad_hours: float | None = None,
                          source: str = "gfw") -> dict:
    """M11: adapt a validated Stage-4 payload to a Stage-5 AIS query.

    Pure mapping — no recomputation of origin or window. Raises ValueError
    when the payload fails boundary validation.
    """
    problems = validate_stage4_output(origin_payload)
    if problems:
        raise ValueError(f"Stage-4 payload rejected at boundary: {problems}")
    origin = origin_payload.get("origin_centroid", origin_payload.get("origin"))
    window = origin_payload.get("release_window") or {}
    start = window.get("start", origin_payload.get("release_time_window_start"))
    end = window.get("end", origin_payload.get("release_time_window_end"))
    w = ReleaseWindow(start=start, end=end)
    return {
        "incident_id": incident_id,
        "origin_lon": float(origin[0]),
        "origin_lat": float(origin[1]),
        "release_time_start": w.start,
        "release_time_end": w.end,
        "radius_km": radius_km,
        "time_pad_hours": time_pad_hours,
        "source": source,
    }


def validate_pipeline_source(ais_source: str) -> None:
    """M11: reject scenario AIS outside sih_demo at job entry, before Stage 4
    runs — drift physics must never be steered by a Stage-5 parameter that
    the current mode forbids."""
    from fastapi import HTTPException

    if ais_source == "scenario":
        from app.config import scenario_allowed

        if not scenario_allowed():
            raise HTTPException(status_code=424, detail={
                "type": "mode-forbidden", "title": "Scenario source forbidden",
                "detail": "source='scenario' requires APP_MODE=sih_demo. Use source='gfw'."})
    elif ais_source != "gfw":
        from fastapi import HTTPException as _H

        raise _H(status_code=422, detail={
            "type": "invalid-source", "title": "Invalid AIS source",
            "detail": "AIS source must be 'gfw' or 'scenario'."})
