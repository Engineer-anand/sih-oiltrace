"""M2 contract tests — pure, no heavy deps (no torch/rasterio/opendrift)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("APP_MODE", "development")

from app.contracts import (
    DataMode,
    ReleaseWindow,
    parse_utc,
    recompute_total,
    renormalize_weights,
    scoring_weights,
    to_iso_z,
    validate_geojson_geometry,
    validate_lonlat,
)


def test_iso_z_roundtrip():
    dt = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    s = to_iso_z(dt)
    assert s.endswith("Z")
    assert parse_utc(s) == dt
    assert parse_utc("2026-09-01T12:00:00+00:00") == dt


def test_release_window_canonical():
    w = ReleaseWindow(start="2026-09-01T00:00:00Z", end="2026-09-02T00:00:00Z")
    j = w.to_json()
    assert set(j) == {"start", "end"}
    assert j["start"].endswith("Z") and j["end"].endswith("Z")
    try:
        ReleaseWindow(start="2026-09-02T00:00:00Z", end="2026-09-01T00:00:00Z")
        raise AssertionError("expected ordering error")
    except Exception:
        pass


def test_centroid_and_coords_lonlat():
    lon, lat = validate_lonlat(72.5, 18.9)
    centroid = [lon, lat]
    assert centroid == [72.5, 18.9]
    try:
        validate_lonlat(200.0, 0.0)
        raise AssertionError("expected range error")
    except ValueError:
        pass


def test_geometry_validation():
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert validate_geojson_geometry(geom) is geom
    try:
        validate_geojson_geometry({"type": "LineString", "coordinates": []})
        raise AssertionError("expected geometry error")
    except ValueError:
        pass


def test_weights_single_source_and_recompute():
    w = scoring_weights()
    assert abs(sum(w.values()) - 1.0) < 1e-9
    comps = {"proximity": 80.0, "timing": 60.0, "trajectory": 50.0,
             "drift_overlap": 70.0, "behavior": 30.0, "vessel_type": 80.0}
    total = recompute_total(comps, w)
    assert total == round(sum(comps[k] * w[k] for k in comps), 2)


def test_renormalize_unavailable():
    w = scoring_weights()
    avail = ["proximity", "timing", "drift_overlap", "vessel_type"]
    rw = renormalize_weights(avail)
    assert abs(sum(rw.values()) - 1.0) < 1e-9
    assert set(rw) == set(avail)
    # renormalized total differs from full-weight total (proves exclusion, not neutrality)
    comps = {"proximity": 80.0, "timing": 60.0, "drift_overlap": 70.0, "vessel_type": 80.0}
    assert recompute_total(comps, rw) != recompute_total({**comps, "trajectory": 50.0, "behavior": 30.0}, w)


def test_data_modes_known():
    assert {m.value for m in DataMode} == {"LIVE", "ARCHIVE", "FALLBACK", "SCENARIO", "DEGRADED", "FAILED"}


def test_validate_job_inputs_bounds():
    from fastapi import HTTPException

    from app.contracts import validate_job_inputs
    validate_job_inputs(24, 20.0)
    validate_job_inputs(1, 0.5)
    validate_job_inputs(168, 500.0)
    for bad in (0, -5, 169, 1000):
        try:
            validate_job_inputs(bad, 20.0)
            raise AssertionError(f"expected 422 for lookback={bad}")
        except HTTPException as exc:
            assert exc.status_code == 422
    for bad in (0, -1.0, 501.0):
        try:
            validate_job_inputs(24, bad)
            raise AssertionError(f"expected 422 for radius={bad}")
        except HTTPException as exc:
            assert exc.status_code == 422


def test_sse_framing_and_resume():
    import json

    from app.contracts import format_sse_event, sse_initial_version

    frame = format_sse_event(7, {"status": "running"})
    assert frame.startswith("id: 7\n")
    assert "\ndata: " in frame
    assert "event: terminal" not in frame
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload == {"status": "running"}

    term = format_sse_event(9, {"status": "done"}, terminal=True)
    assert term.startswith("id: 9\nevent: terminal\n")

    assert sse_initial_version("12") == 12
    assert sse_initial_version(None) == -1
    assert sse_initial_version("bogus") == -1
