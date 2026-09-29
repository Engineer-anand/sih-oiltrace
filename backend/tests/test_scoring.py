"""M9 candidate-matching tests — capability gating, renormalization, overlap."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from app.schemas.ais import VesselInfo, VesselTrackPoint  # noqa: E402
from app.services import scoring_service as sc  # noqa: E402

_T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)
_MID = _T0 + (_T1 - _T0) / 2


def _vessel(with_motion: bool) -> VesselInfo:
    pts = []
    for h in (11, 12, 13):
        t = _T0 + timedelta(hours=h)
        pts.append(VesselTrackPoint(time=t, lon=72.31, lat=18.72,
                                   speed_kn=8.0 if with_motion else None,
                                   heading_deg=90.0 if with_motion else None))
    return VesselInfo(vessel_id="V-1", mmsi="352001842", name="T", vessel_type="tanker",
                      flag="PA", length_m=200.0, track=pts)


def _trajs():
    out = []
    for k in range(5):
        out.append([
            {"time": (_T0 + timedelta(hours=h)).isoformat(), "lon": 72.30 + 0.001 * k, "lat": 18.71}
            for h in range(0, 25)
        ])
    return out


def test_presence_factors_unavailable_and_renormalized():
    vessels = [_vessel(False)]
    scores = sc.score_vessels(vessels, 72.3, 18.7, _T0, _T1, [(72.3, 18.7)],
                              capabilities=["AIS_PRESENCE"], trajectories=_trajs())
    vs = scores[0]
    by = {f.factor: f for f in vs.factors}
    assert by["trajectory"].status == "UNAVAILABLE"
    assert by["behavior"].status == "UNAVAILABLE"
    assert by["proximity"].status == "COMPUTED"
    # no neutral constants anywhere
    assert by["trajectory"].raw_score == 0.0 and by["behavior"].raw_score == 0.0
    assert set(vs.unavailable_factors) == {"trajectory", "behavior"}
    assert abs(sum(vs.applied_weights.values()) - 1.0) < 1e-9
    assert set(vs.applied_weights) == {"proximity", "timing", "drift_overlap", "vessel_type"}
    # total recomputes exactly from every available factor (only UNAVAILABLE
    # excluded; DEGRADED drift_overlap still carries its applied weight here)
    expect = round(sum(f.weighted_score for f in vs.factors if f.status != "UNAVAILABLE"), 2)
    assert vs.total_score == expect
    by["drift_overlap"]  # trajectories present -> COMPUTED, included above
    assert abs(sum(vs.applied_weights.values()) - 1.0) < 1e-9


def test_track_capability_computes_motion():
    vessels = [_vessel(True)]
    scores = sc.score_vessels(vessels, 72.3, 18.7, _T0, _T1, [(72.3, 18.7)],
                              capabilities=["AIS_TRACK"], trajectories=_trajs())
    by = {f.factor: f for f in scores[0].factors}
    assert by["trajectory"].status == "COMPUTED"
    assert by["behavior"].status == "COMPUTED"
    assert scores[0].unavailable_factors == []
    assert abs(sum(scores[0].applied_weights.values()) - 1.0) < 1e-9


def test_overlap_time_sensitive():
    trajs = _trajs()
    near = _vessel(False)
    far_time = VesselInfo(vessel_id="V-2", mmsi="1", name="F", vessel_type="cargo",
                          flag="PA", length_m=100.0, track=[
                              VesselTrackPoint(time=_T1 + timedelta(hours=30), lon=72.31, lat=18.72,
                                               speed_kn=None, heading_deg=None)])
    a = sc.score_vessels([near], 72.3, 18.7, _T0, _T1, [(72.3, 18.7)],
                         capabilities=["AIS_PRESENCE"], trajectories=trajs)[0]
    b = sc.score_vessels([far_time], 72.3, 18.7, _T0, _T1, [(72.3, 18.7)],
                         capabilities=["AIS_PRESENCE"], trajectories=trajs)[0]
    sa = next(f.raw_score for f in a.factors if f.factor == "drift_overlap")
    sb = next(f.raw_score for f in b.factors if f.factor == "drift_overlap")
    assert sa > sb  # in-window fixes overlap; out-of-window fixes do not


def test_overlap_degraded_without_trajectories():
    vessels = [_vessel(False)]
    scores = sc.score_vessels(vessels, 72.3, 18.7, _T0, _T1, [(72.31, 18.72)],
                              capabilities=["AIS_PRESENCE"], trajectories=[])
    by = {f.factor: f for f in scores[0].factors}
    assert by["drift_overlap"].status == "DEGRADED"
    # Release audit: DEGRADED factors are real, approximate data — they must
    # contribute their applied weight to the total (only UNAVAILABLE factors
    # are excluded), otherwise Σ applied weights != the set actually summed
    # and report/API recomputation drifts from the stored score.
    expect = round(sum(f.weighted_score for f in scores[0].factors
                       if f.status != "UNAVAILABLE"), 2)
    assert scores[0].total_score == expect
    included = [f for f in scores[0].factors if f.status != "UNAVAILABLE"]
    assert abs(sum(f.weight for f in included) - 1.0) < 1e-9
    assert "drift_overlap" not in scores[0].unavailable_factors


def test_evidence_marks_unavailable():
    from app.services.evidence_service import build_evidence

    vessels = [_vessel(False)]
    scores = sc.score_vessels(vessels, 72.3, 18.7, _T0, _T1, [(72.3, 18.7)],
                              capabilities=["AIS_PRESENCE"], trajectories=_trajs())
    ev = build_evidence(scores[0])
    traj = next(i for i in ev.checklist if i.factor == "trajectory")
    assert traj.log_type == "info" and "unavailable" in traj.description
