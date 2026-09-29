"""M11 Stage 4 -> Stage 5 integration tests — boundary contract, no recompute."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from app.config import settings  # noqa: E402
from app.contracts import (  # noqa: E402
    origin_to_ais_request,
    validate_pipeline_source,
    validate_stage4_output,
)
from app.services.drift import estimate_to_payload, run_scenario_backward_drift  # noqa: E402

_T = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _payload():
    est = run_scenario_backward_drift((72.3, 18.7), _T, 24)
    return estimate_to_payload(est)


def test_payload_shape_complete():
    p = _payload()
    assert validate_stage4_output(p) == []
    assert p["origin"] == list(p["origin_centroid"])
    assert set(p["release_window"]) == {"start", "end"}
    assert p["seed"] == 20260905
    assert p["env_mode"] == "SCENARIO"


def test_validator_rejects_gaps():
    p = _payload()
    bad = dict(p)
    del bad["origin_centroid"]
    del bad["origin"]
    assert any("origin" in x for x in validate_stage4_output(bad))
    bad2 = dict(p)
    bad2["release_window"] = {"start": p["release_window"]["end"], "end": p["release_window"]["start"]}
    del bad2["release_time_window_start"]
    del bad2["release_time_window_end"]
    assert any("window" in x for x in validate_stage4_output(bad2))
    bad3 = dict(p)
    bad3["data_mode"] = "LIVE-ISH"
    assert any("data_mode" in x for x in validate_stage4_output(bad3))
    assert validate_stage4_output("nope") != []


def test_adapter_preserves_values_exactly():
    from app.schemas.ais import AisSearchRequest

    p = _payload()
    req = origin_to_ais_request(p, "OS-12345678", radius_km=20.0, time_pad_hours=6.0, source="scenario")
    assert req["origin_lon"] == p["origin_centroid"][0]
    assert req["origin_lat"] == p["origin_centroid"][1]
    model = AisSearchRequest(**{**req, "release_time_start": req["release_time_start"],
                                "release_time_end": req["release_time_end"]})
    assert model.origin_lon == p["origin"][0]
    try:
        origin_to_ais_request({"origin_centroid": [0, 0]}, "OS-1")
        raise AssertionError("expected ValueError for incomplete payload")
    except ValueError:
        pass


def test_pipeline_source_entry_rule():
    from fastapi import HTTPException

    old = settings.APP_MODE
    try:
        settings.APP_MODE = "development"
        try:
            validate_pipeline_source("scenario")
            raise AssertionError("expected 424")
        except HTTPException as exc:
            assert exc.status_code == 424
        validate_pipeline_source("gfw")
        try:
            validate_pipeline_source("bogus")
            raise AssertionError("expected 422")
        except HTTPException as exc:
            assert exc.status_code == 422
        settings.APP_MODE = "sih_demo"
        validate_pipeline_source("scenario")
    finally:
        settings.APP_MODE = old


def test_stage45_chain_end_to_end():
    """Stage-4 payload -> adapter -> AIS (scenario) -> scoring -> evidence,
    asserting the handoff preserves values and totals recompute exactly."""
    from app.services import ais_service as ais
    from app.services import scoring_service as sc
    from app.services.evidence_service import build_evidence_for_all

    old = settings.APP_MODE
    settings.APP_MODE = "sih_demo"
    try:
        p = _payload()
        req = origin_to_ais_request(p, "OS-E2E2E2E2", radius_km=20.0, time_pad_hours=6.0, source="scenario")
        fetched = ais.get_vessels_near_origin(
            origin_lon=req["origin_lon"], origin_lat=req["origin_lat"],
            release_time_start=req["release_time_start"], release_time_end=req["release_time_end"],
            radius_km=req["radius_km"], time_pad_hours=req["time_pad_hours"], source=req["source"])
        assert fetched.mode == "SCENARIO" and fetched.vessels
        assert (req["origin_lon"], req["origin_lat"]) == tuple(p["origin"])
        scores = sc.score_vessels(
            fetched.vessels, req["origin_lon"], req["origin_lat"],
            req["release_time_start"], req["release_time_end"],
            p["final_particle_positions"], radius_km=req["radius_km"],
            capabilities=fetched.capabilities, trajectories=p["particle_trajectories"])
        assert scores and scores[0].rank == 1
        for vs in scores:
            # Available factors (COMPUTED + DEGRADED) must recompute exactly.
            expect = round(sum(f.weighted_score for f in vs.factors if f.status != "UNAVAILABLE"), 2)
            assert vs.total_score == expect
        evidence = build_evidence_for_all(scores)
        assert len(evidence) == len(scores)
        assert all(len(ve.checklist) == len(scores[0].factors) for ve in evidence)
    finally:
        settings.APP_MODE = old
