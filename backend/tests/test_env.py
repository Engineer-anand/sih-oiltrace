"""M5 environmental-data tests — pure helpers + synthetic-field QC, no network."""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from app.services import ocean_data as od  # noqa: E402


def test_era5_month_groups_single_month():
    s = datetime(2026, 1, 10, 0, 0)
    e = datetime(2026, 1, 12, 12, 0)
    groups = od._era5_month_groups(s, e)
    assert len(groups) == 1
    assert groups[0]["year"] == "2026" and groups[0]["month"] == "01"
    assert groups[0]["day"] == ["10", "11", "12"]


def test_era5_month_groups_cross_boundary():
    s = datetime(2026, 1, 31, 12, 0)
    e = datetime(2026, 2, 2, 6, 0)
    groups = od._era5_month_groups(s, e)
    assert len(groups) == 2
    jan = next(g for g in groups if g["month"] == "01")
    feb = next(g for g in groups if g["month"] == "02")
    assert jan["day"] == ["31"] and feb["day"] == ["01", "02"]


def test_era5_month_groups_cross_year():
    s = datetime(2025, 12, 31, 0, 0)
    e = datetime(2026, 1, 1, 12, 0)
    groups = od._era5_month_groups(s, e)
    assert {(g["year"], g["month"]) for g in groups} == {("2025", "12"), ("2026", "01")}


def test_worst_mode_ordering():
    assert od._worst_mode("LIVE", "ARCHIVE") == "ARCHIVE"
    assert od._worst_mode("FALLBACK", "SCENARIO") == "SCENARIO"
    assert od._worst_mode("LIVE", "LIVE") == "LIVE"


def test_resolve_failure_dev_fallback_and_prod_refusal():
    from app.config import settings

    tmp = Path(tempfile.mkdtemp(prefix="od_test_"))
    out = tmp / "field.nc"
    s = datetime(2026, 1, 10, tzinfo=timezone.utc)
    e = datetime(2026, 1, 11, tzinfo=timezone.utc)

    old_mode = settings.APP_MODE
    try:
        settings.APP_MODE = "development"
        res = od._resolve_failure(
            "CMEMS-test", "missing_credentials",
            lambda: out,  # fake synth: mode selection only, no file I/O
            {},
        )
        assert res.mode == "FALLBACK" and res.using_synthetic is True

        settings.APP_MODE = "sih_demo"
        res2 = od._resolve_failure("CMEMS-test", "missing_credentials", lambda: out, {})
        assert res2.mode == "SCENARIO"

        settings.APP_MODE = "production"
        try:
            od._resolve_failure("CMEMS-test", "missing_credentials", lambda: out, {})
            raise AssertionError("expected RealDataUnavailableError in production")
        except Exception as exc:
            assert type(exc).__name__ == "RealDataUnavailableError"
    finally:
        settings.APP_MODE = old_mode


def test_synthetic_field_passes_qc():
    try:
        import xarray  # noqa: F401
    except ImportError:
        print("SKIP synthetic-field QC (xarray unavailable in this env)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="od_qc_"))
    out = tmp / "qc.nc"
    s = datetime(2026, 1, 10, tzinfo=timezone.utc)
    e = datetime(2026, 1, 11, tzinfo=timezone.utc)
    od._synthetic_field(out, 72.0, 18.0, 73.0, 19.0, s, e, var_names=("uo", "vo"))
    assert od._file_has_cf_variables(out, od._CURRENT_STD_NAMES)
    assert od._time_coverage_ok(out, s, e, tol_hours=6)
    assert od._spatial_coverage_ok(out, 72.0, 18.0, 73.0, 19.0)
    assert od._range_ok(out, od._CURRENT_STD_NAMES, "current")
    assert od._file_quality_ok(out, od._CURRENT_STD_NAMES, "current",
                               72.0, 18.0, 73.0, 19.0, s, e, tol_hours=6)
    # uncovered window must fail coverage
    far = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert od._time_coverage_ok(out, far, far + timedelta(hours=6), tol_hours=6) is False
