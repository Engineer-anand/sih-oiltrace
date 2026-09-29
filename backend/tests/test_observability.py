"""M15 observability tests — pure, no app imports beyond observability."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.observability import (  # noqa: E402
    Metrics,
    check_readiness,
    metrics,
    new_request_id,
)


def test_request_id_format():
    rid = new_request_id()
    assert len(rid) == 12 and all(c in "0123456789abcdef" for c in rid)


def test_metrics_counters_and_latency():
    m = Metrics()
    m.incr("job_done_total")
    m.incr("job_done_total", 2)
    m.observe("api_latency:/x", 10.0)
    m.observe("api_latency:/x", 20.0)
    m.observe("api_latency:/x", 30.0)
    snap = m.snapshot()
    assert snap["counters"]["job_done_total"] == 3
    lat = snap["latency_ms"]["api_latency:/x"]
    assert lat["count"] == 3 and lat["p50"] == 20.0 and lat["max"] == 30.0
    m.reset()
    assert m.snapshot() == {"counters": {}, "latency_ms": {}}


def test_shared_registry_usable():
    metrics.incr("synthetic_fallback_total")
    assert metrics.snapshot()["counters"]["synthetic_fallback_total"] >= 1
    metrics.reset()


def test_readiness_logic():
    ok = check_readiness(True, True, True, {"gfw": {"configured": False}})
    assert ok["status"] == "ready"
    assert ok["dependencies"]["provider:gfw"] == "unconfigured"
    bad_db = check_readiness(False, True, True, {})
    assert bad_db["status"] == "not-ready"
    assert bad_db["dependencies"]["database"] == "fail"
    stale = check_readiness(True, True, True, {"cmems": {"configured": True, "fresh": False}})
    assert stale["dependencies"]["provider:cmems"] == "stale"
    assert stale["status"] == "not-ready"
    assert "timestamp" in ok
