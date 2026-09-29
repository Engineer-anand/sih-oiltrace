"""M7 AIS provider tests — no network, no credentials needed."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from app.config import settings  # noqa: E402
from app.errors import RealDataUnavailableError  # noqa: E402
from app.providers.ais.base import ProviderShapeError  # noqa: E402
from app.providers.ais.gfw_presence import GfwPresenceProvider  # noqa: E402
from app.services import ais_service as ais  # noqa: E402

_T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)


def _mode(mode):
    old = settings.APP_MODE
    settings.APP_MODE = mode
    return old


def test_scenario_refused_outside_demo():
    old = _mode("development")
    try:
        try:
            ais.get_vessels_near_origin(72.3, 18.7, _T0, _T1, source="scenario")
            raise AssertionError("expected RealDataUnavailableError")
        except RealDataUnavailableError:
            pass
    finally:
        settings.APP_MODE = old


def test_scenario_fleet_honest_labels():
    old = _mode("sih_demo")
    try:
        res = ais.get_vessels_near_origin(72.3, 18.7, _T0, _T1, source="scenario")
        assert res.mode == "SCENARIO"
        assert len(res.vessels) == 7
        assert res.capabilities == ["AIS_PRESENCE"]
        assert res.unavailable_factors == ["trajectory", "behavior"]
        for v in res.vessels:
            assert v.mmsi.startswith("SCENARIO-"), v.mmsi
            assert v.mmsi.isupper() and not any(c.isdigit() and len(v.mmsi) == 9 for c in v.mmsi)
            assert "(scenario)" in (v.name or "")
    finally:
        settings.APP_MODE = old


def test_gfw_no_token_dev_fallback_honest():
    old = _mode("development")
    old_token, settings.GFW_API_TOKEN = settings.GFW_API_TOKEN, None
    try:
        a = ais.get_vessels_near_origin(72.3, 18.7, _T0, _T1, source="gfw")
        b = ais.get_vessels_near_origin(72.3, 18.7, _T0, _T1, source="gfw")
        assert a.mode == "FALLBACK" and a.using_fallback is True
        assert a.warnings
        assert [v.vessel_id for v in a.vessels] == [v.vessel_id for v in b.vessels]
        for v in a.vessels:
            assert v.mmsi.startswith("FALLBACK-"), v.mmsi
            assert "(fallback)" in (v.name or "")
            assert len(v.mmsi) != 9 or not v.mmsi.isdigit()
    finally:
        settings.GFW_API_TOKEN = old_token
        settings.APP_MODE = old


def test_gfw_no_token_production_raises():
    old = _mode("production")
    old_token, settings.GFW_API_TOKEN = settings.GFW_API_TOKEN, None
    try:
        try:
            ais.get_vessels_near_origin(72.3, 18.7, _T0, _T1, source="gfw")
            raise AssertionError("expected RealDataUnavailableError in production")
        except RealDataUnavailableError:
            pass
    finally:
        settings.GFW_API_TOKEN = old_token
        settings.APP_MODE = old


def test_gfw_shape_validation():
    import httpx

    from app.config import settings as _s

    old_token, _s.GFW_API_TOKEN = _s.GFW_API_TOKEN, "dummy-test-token"
    real_post = httpx.post

    class _Resp:
        def raise_for_status(self):
            pass

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    try:
        httpx.post = lambda *a, **k: _Resp({"entries": "garbage"})
        try:
            GfwPresenceProvider().search(72, 18, 73, 19, _T0, _T1)
            raise AssertionError("expected ProviderShapeError")
        except ProviderShapeError:
            pass
        httpx.post = lambda *a, **k: _Resp({"entries": [{"junk": 1}]})
        try:
            GfwPresenceProvider().search(72, 18, 73, 19, _T0, _T1)
            raise AssertionError("expected ProviderShapeError for unparseable rows")
        except ProviderShapeError:
            pass
        httpx.post = lambda *a, **k: _Resp({"entries": []})
        res = GfwPresenceProvider().search(72, 18, 73, 19, _T0, _T1)
        assert res.positions == [] and res.mode == "LIVE"
    finally:
        httpx.post = real_post
        _s.GFW_API_TOKEN = old_token


def test_radius_pad_defaults_from_settings():
    assert ais._resolve_radius(None) == settings.AIS_SEARCH_RADIUS_KM
    assert ais._resolve_pad(None) == settings.AIS_TIME_PAD_HOURS
    assert ais._resolve_radius(33.0) == 33.0
