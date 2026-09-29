"""
Database engine + session management.

Works against SQLite (zero-setup default) or PostgreSQL/PostGIS (set
DATABASE_URL in .env) with no code changes — geometries are stored as
GeoJSON text columns rather than native PostGIS geometry types, which
keeps the schema portable across both backends while still being fully
queryable/exportable as GeoJSON by the API layer.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

_db_url = settings.DATABASE_URL
_connect_args = {"check_same_thread": False} if _db_url.startswith("sqlite") else {}

try:
    engine = create_engine(_db_url, connect_args=_connect_args, future=True)
    # Eagerly probe DBAPI import so missing driver falls back immediately
    _ = engine.dialect.dbapi
except Exception as exc:
    logger.warning("DATABASE WARNING | Could not load driver for %s (%s). Falling back to SQLite.", _db_url, exc)
    _db_url = f"sqlite:///{settings.DATA_DIR / 'oiltrace.db'}"
    engine = create_engine(_db_url, connect_args={"check_same_thread": False}, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def ensure_columns(bind_engine=None) -> None:
    """M6: additive, idempotent column migration for SQLite and PostgreSQL.

    create_all covers fresh databases; this covers pre-existing files (e.g.
    data/oiltrace.db) that predate newer columns. Only ADD COLUMNs, never
    destructive changes.
    """
    from sqlalchemy import inspect, text

    eng = bind_engine if bind_engine is not None else engine
    if eng is None:
        return
    wanted: dict[str, list[tuple[str, str]]] = {
        "drift_runs": [
            ("seed", "INTEGER"),
            ("quality_score", "FLOAT"),
            ("origin_probability", "FLOAT"),
            ("uncertainty_ellipse_json", "TEXT"),
            ("env_mode", "VARCHAR(16)"),
            ("warnings_json", "TEXT"),
        ],
        "spills": [
            ("source_type", "VARCHAR"),
            ("aws_scene_id", "VARCHAR"),
            ("model_diagnostics_json", "TEXT"),
            ("data_mode", "VARCHAR(16)"),
            ("bbox_min_lon", "FLOAT"),
            ("bbox_min_lat", "FLOAT"),
            ("bbox_max_lon", "FLOAT"),
            ("bbox_max_lat", "FLOAT"),
        ],
        "vessel_tracks": [
            ("spill_id", "VARCHAR"),
        ],
        "scores": [
            ("unavailable_json", "TEXT"),
            ("applied_weights_json", "TEXT"),
            ("degraded_json", "TEXT"),
        ],
        "jobs": [],
        "job_stages": [],
    }
    try:
        insp = inspect(eng)
        existing_tables = set(insp.get_table_names())
    except Exception as exc:
        logger.warning("MIGRATION SKIP | cannot inspect database: %s", exc)
        return
    with eng.begin() as conn:
        for table, cols in wanted.items():
            if table not in existing_tables or not cols:
                continue
            try:
                present = {c["name"] for c in insp.get_columns(table)}
            except Exception:
                continue
            for name, ddl in cols:
                if name in present:
                    continue
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    logger.info("MIGRATION | added %s.%s", table, name)
                except Exception as exc:
                    logger.warning("MIGRATION WARNING | %s.%s | error=%s", table, name, exc)


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call on every startup."""
    from app.models import db_models  # noqa: F401 — registers models with Base.metadata

    if engine.url.get_backend_name() == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        except Exception as exc:
            logger.warning("DATABASE WARNING | PostgreSQL postgis extension init failed: %s", exc)
    db_models.Base.metadata.create_all(bind=engine)
    ensure_columns(engine)
    # PostGIS-native geometry column + spatial index (not portable; postgres only).
    with engine.begin() as conn:
        if engine.url.get_backend_name() == "postgresql":
            try:
                conn.execute(text("ALTER TABLE spills ADD COLUMN IF NOT EXISTS geom geometry(Geometry,4326)"))
            except Exception as exc:
                logger.warning("MIGRATION WARNING | spills.geom | error=%s", exc)
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_spills_geom_gist ON spills USING GIST (geom)"))
            except Exception as exc:
                logger.warning("MIGRATION WARNING | GIST index on spills.geom | error=%s", exc)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager for use outside of FastAPI request handlers (scripts, tests)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
