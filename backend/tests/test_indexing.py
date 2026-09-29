"""M13 indexing tests — index existence + query-plan usage on SQLite.

PostGIS GIST/vector-tile/partitioning notes:
- spills.geom GIST is created by init_db on PostgreSQL (see app/db.py).
- Table partitioning and vector tiles are deliberately deferred: single-node
  SIH has no measured bottleneck; see milestone report.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

_TMP = tempfile.mkdtemp(prefix="oiltrace_idx_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test_idx.db"

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})

from app.models import db_models  # noqa: E402

db_models.Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _indexes(table):
    from sqlalchemy import inspect

    return {ix["name"]: ix for ix in inspect(engine).get_indexes(table)}


def test_ais_position_indexes_exist():
    ix = _indexes("ais_positions")
    assert "idx_ais_positions_mmsi_time" in ix
    assert "idx_ais_positions_time" in ix
    assert "idx_ais_positions_lon_lat_time" in ix


def test_related_table_indexes_exist():
    assert "idx_spills_created" in _indexes("spills")
    assert "idx_drift_runs_spill" in _indexes("drift_runs")
    assert "idx_scores_spill" in _indexes("scores")
    assert "idx_evidence_spill" in _indexes("evidence_logs")
    assert "idx_job_stages_job_seq" in _indexes("job_stages")
    assert "idx_vessel_tracks_vessel_time" in _indexes("vessel_tracks")


def test_mmsi_time_query_uses_index():
    from datetime import datetime, timezone

    db = Session()
    try:
        for h in range(50):
            db.add(db_models.AisPosition(
                mmsi="123456789", identity_key="123456789", shipname="T",
                ts=datetime(2026, 9, 1, h % 24, tzinfo=timezone.utc),
                lon=72.3, lat=18.7, source="gfw", quality="live"))
        db.commit()
        plan = db.execute(text(
            "EXPLAIN QUERY PLAN SELECT * FROM ais_positions "
            "WHERE mmsi = '123456789' AND ts >= '2026-09-01' ORDER BY ts")).fetchall()
        text_plan = " ".join(str(r) for r in plan)
        assert "USING INDEX" in text_plan, text_plan
    finally:
        db.close()


def test_production_refuses_sqlite():
    from app.config import settings, validate_app_mode

    old_mode, old_url = settings.APP_MODE, settings.DATABASE_URL
    old_cors = os.environ.get("CORS_ORIGINS")
    try:
        settings.APP_MODE = "staging"
        settings.DATABASE_URL = "sqlite:///x.db"
        os.environ["CORS_ORIGINS"] = "https://example.com"
        try:
            validate_app_mode()
            raise AssertionError("expected refusal of SQLite in staging")
        except RuntimeError as exc:
            assert "SQLite" in str(exc)
        settings.APP_MODE = "production"
        settings.ALLOW_LIVE_FALLBACK = False
        settings.OILTRACE_API_KEY = "test-key"
        try:
            validate_app_mode()
            raise AssertionError("expected refusal of SQLite in production")
        except RuntimeError as exc:
            assert "SQLite" in str(exc)
    finally:
        settings.APP_MODE = old_mode
        settings.DATABASE_URL = old_url
        if old_cors is None:
            os.environ.pop("CORS_ORIGINS", None)
        else:
            os.environ["CORS_ORIGINS"] = old_cors
