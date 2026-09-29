"""Final SIH release-audit regression tests (release validation, Phase 17).

Locks confirmed fixes from the final code review:
  1. Canonical UTC ISO-8601 'Z' timestamps end-to-end: stage-4 payload,
     numpy trajectory extraction (was timezone-suffix-less, which shifted
     by local offset in the UI), spill detail/list API, and job JSON
     (was json.dumps(default=str) -> 'YYYY-MM-DD HH:MM:SS+00:00').
  2. DEGRADED scoring-factor status persists (Score.degraded_json) and
     round-trips through get_spill_detail (factor_list status, degraded
     list, grade), and stored total == recomputation from stored
     components x applied weights including the degraded factor.
"""
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from app.db import engine, session_scope  # noqa: E402
from app.models import db_models  # noqa: E402

db_models.Base.metadata.create_all(bind=engine)

_SPILL = "OS-A0D17E5A"  # must satisfy the OS-XXXXXXXX hex route validation
_VESSEL = "V-A0D17E5A"


def test_stage4_payload_timestamps_are_utc_z():
    from app.services.drift import estimate_to_payload, run_scenario_backward_drift

    t = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    est = run_scenario_backward_drift((72.3, 18.7), t, 24)
    p = estimate_to_payload(est)
    for key in ("release_time_window_start", "release_time_window_end"):
        assert p[key].endswith("Z"), f"{key}={p[key]}"
    assert p["release_window"]["start"].endswith("Z")
    assert p["release_window"]["end"].endswith("Z")
    for traj in p.get("particle_trajectories") or []:
        for pt in traj:
            assert str(pt["time"]).endswith("Z"), pt["time"]


def test_numpy_trajectory_extraction_emits_utc_z():
    import numpy as np

    from app.services.drift import _extract_particle_trajectories

    times = np.array(["2026-09-01T00:00:00", "2026-09-01T01:00:00"], dtype="datetime64[s]")
    sim = SimpleNamespace(result=SimpleNamespace(
        lon=SimpleNamespace(values=np.array([[72.30, 72.31], [72.32, 72.33]])),
        lat=SimpleNamespace(values=np.array([[18.70, 18.71], [18.72, 18.73]])),
        time=SimpleNamespace(values=times),
    ))
    out = _extract_particle_trajectories(sim)
    assert out
    for traj in out:
        for pt in traj:
            # Regression: numpy's default string form carries no suffix, so the
            # old code emitted bare 'YYYY-MM-DDTHH:MM:SS' (local-shift in JS).
            assert str(pt["time"]).endswith("Z"), pt["time"]


def test_spill_detail_round_trip_z_and_degraded_status():
    from app.api.pipeline import get_spill_detail, list_spills
    from app.models.db_models import Score, Spill, Vessel

    # Products are exact at 2dp so sum-of-rounded and round-of-sum agree.
    applied = {"proximity": 0.30, "timing": 0.25, "drift_overlap": 0.25, "vessel_type": 0.20}
    raw = {"proximity": 80.0, "timing": 60.0, "trajectory": 0.0,
           "drift_overlap": 44.0, "behavior": 0.0, "vessel_type": 50.0}
    total = round(sum(applied[k] * raw[k] for k in applied), 2)
    try:
        with session_scope() as db:
            # idempotent re-run
            db.query(Score).filter(Score.spill_id == _SPILL).delete()
            db.query(Vessel).filter(Vessel.id == _VESSEL).delete()
            db.query(Spill).filter(Spill.id == _SPILL).delete()
            db.add(Spill(id=_SPILL, detected_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
                         geom_json='{"type": "FeatureCollection", "features": []}',
                         area_km2=1.25, centroid_lon=72.3, centroid_lat=18.7,
                         confidence=0.81, satellite="Sentinel-1",
                         source_filename="s1_aud.tif", data_mode="SCENARIO"))
            db.add(Vessel(id=_VESSEL, mmsi="000000000", name="Audit fixture", vessel_type="cargo"))
            db.add(Score(spill_id=_SPILL, vessel_id=_VESSEL, total_score=total,
                         proximity_score=raw["proximity"], timing_score=raw["timing"],
                         trajectory_score=raw["trajectory"], drift_overlap_score=raw["drift_overlap"],
                         behavior_score=raw["behavior"], vessel_type_score=raw["vessel_type"],
                         rank=1,
                         unavailable_json=json.dumps(["trajectory", "behavior"]),
                         applied_weights_json=json.dumps(applied),
                         degraded_json=json.dumps(["drift_overlap"])))
        with session_scope() as db:
            detail = get_spill_detail(_SPILL, db)
            rows = list_spills(db, limit=100, offset=0)

        # canonical UTC Z on every serialized timestamp
        assert detail["spill"]["detected_at"].endswith("Z")
        row = next(r for r in rows if r["id"] == _SPILL)
        assert row["detected_at"].endswith("Z")
        assert row["data_mode"] == "SCENARIO"

        v = detail["vessels"][0]
        assert set(v["degraded"]) == {"drift_overlap"}
        statuses = {f["factor"]: f["status"] for f in v["factor_list"]}
        assert statuses["drift_overlap"] == "DEGRADED"
        assert statuses["trajectory"] == "UNAVAILABLE"
        assert statuses["proximity"] == "COMPUTED"
        # grade now reflects the degraded factor (was hardcoded n_degraded=0)
        assert v["confidence"] == "medium" and v["data_quality"] == "reduced"
        # Phase-6 contract: stored total == recomputation from stored
        # components x applied weights (UNAVAILABLE excluded, DEGRADED kept)
        recomputed = round(sum(f["weight"] * f["raw_score"] for f in v["factor_list"]
                               if f["status"] != "UNAVAILABLE"), 2)
        assert recomputed == v["total_score"]
        assert abs(sum(f["weight"] for f in v["factor_list"]
                       if f["status"] != "UNAVAILABLE") - 1.0) < 1e-9
    finally:
        with session_scope() as db:
            db.query(Score).filter(Score.spill_id == _SPILL).delete()
            db.query(Vessel).filter(Vessel.id == _VESSEL).delete()
            db.query(Spill).filter(Spill.id == _SPILL).delete()


def test_docker_entrypoint_repairs_volume_and_drops_privileges():
    """The data volume may be root-owned (stale, from an older image).

    The container must repair ownership at boot and still run the server
    unprivileged; `USER oiltrace` as the image default would make the
    repair impossible and crash-loop the stack on mkdir(EACCES).
    """
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    with open(os.path.join(root, "docker", "entrypoint.sh"), encoding="utf-8") as fh:
        entrypoint = fh.read()
    assert "ENTRYPOINT [\"/entrypoint.sh\"]" in dockerfile
    assert "USER oiltrace" not in dockerfile  # would skip ownership repair
    assert entrypoint.lstrip().startswith("#!")
    assert "setpriv --reuid=10001 --regid=10001 --init-groups" in entrypoint
    assert "chown oiltrace:oiltrace /app/data" in entrypoint


def test_job_json_datetimes_serialized_as_utc_z():
    from sqlalchemy.orm import sessionmaker

    from app.services.job_store import job_store

    # Standalone-safe: configure the same commit-per-operation factory the
    # job suite uses (already configured when run under run_all).
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    @contextmanager
    def _factory():
        db = Session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    job_store.configure_session(_factory)
    stamp = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
    jid = job_store.create(params={"detected_at": stamp})
    try:
        job_store.start_stage(jid, "detection")
        job_store.finish_stage(jid, "detection", partial={"incident": {"detected_at": stamp}})
        job = job_store.get(jid)
        got = job["partial"]["incident"]["detected_at"]
        # Regression: default=str produced '2026-09-01 10:30:00+00:00'
        # (space separator, offset form) instead of canonical ISO-8601 Z.
        assert got.endswith("Z") and "T" in got, got
        job_store.fail_stage(jid, "detection", "audit cleanup")
        job_store.fail(jid, "audit cleanup")
    finally:
        job_store.configure_session(_factory)
