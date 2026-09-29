"""M8 historical-AIS tests — temp SQLite, no network."""
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp(prefix="oiltrace_hist_test_")
os.environ.setdefault("APP_MODE", "development")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test_hist.db"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})

from app.models import db_models  # noqa: E402

db_models.Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def _session():
    db = Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


from app.providers.ais.base import RawPosition  # noqa: E402
from app.services import ais_service as ais  # noqa: E402

_T = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


def _pos(mmsi, hours, lon=72.3, lat=18.7, **kw):
    return RawPosition(mmsi=mmsi, name=kw.get("name", "Test Ship"), vessel_type="tanker",
                       flag="PA", length_m=100.0, time=_T + timedelta(hours=hours),
                       lon=lon, lat=lat, speed_kn=None, heading_deg=None)


def test_identity_resolution():
    real = ais.resolve_identity("352001842", "  MV Meridian ")
    assert real == {"identity_key": "352001842", "mmsi": "352001842",
                    "synthetic": False, "display_name": "MV Meridian"}
    scen = ais.resolve_identity("SCENARIO-01", "MV Meridian (scenario)")
    assert scen["synthetic"] is True and scen["identity_key"] == "SCENARIO-01"
    fb = ais.resolve_identity("fallback-03")
    assert fb["synthetic"] is True


def test_persist_dedupes():
    with _session() as db:
        n1 = ais.persist_positions([_pos("111111111", 0), _pos("111111111", 1)], source="gfw", quality="live", db=db)
        n2 = ais.persist_positions([_pos("111111111", 0), _pos("111111111", 1)], source="gfw", quality="live", db=db)
        assert (n1, n2) == (2, 0)


def test_history_excludes_synthetic_by_default():
    with _session() as db:
        ais.persist_positions([_pos("SCENARIO-09", 0)], source="scenario", quality="synthetic", db=db)
        live = ais.query_history("SCENARIO-09", _T - timedelta(days=1), _T + timedelta(days=1), db=db)
        assert live == []
        inc = ais.query_history("SCENARIO-09", _T - timedelta(days=1), _T + timedelta(days=1),
                                include_synthetic=True, db=db)
        assert len(inc) == 1 and inc[0]["quality"] == "synthetic"


def test_build_tracks_gap_and_order():
    positions = [
        {"ts": (_T + timedelta(hours=h)).isoformat(), "lon": 72.3, "lat": 18.7,
         "speed_kn": None, "heading_deg": None, "source": "gfw", "quality": "live"}
        for h in (0, 1, 2, 20, 21)
    ]
    tracks = ais.build_tracks(positions, gap_hours=6)
    assert tracks["segment_count"] == 2
    assert tracks["fix_count"] == 5
    assert [len(s) for s in tracks["segments"]] == [3, 2]
    assert tracks["last_seen"] == positions[-1]["ts"]
    assert tracks["stale"] in (True, False)


def test_stale_detection():
    old = [{"ts": (_T - timedelta(days=60)).isoformat(), "lon": 0.0, "lat": 0.0,
            "speed_kn": None, "heading_deg": None, "source": "gfw", "quality": "live"}]
    assert ais.build_tracks(old)["stale"] is True
    assert ais.build_tracks([]) == {"segments": [], "segment_count": 0, "fix_count": 0,
                                    "last_seen": None, "stale": False}
