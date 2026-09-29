"""M3 durable-job tests — temp SQLite file, injected session factory, no server."""
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp(prefix="oiltrace_jobs_test_")
os.environ.setdefault("APP_MODE", "development")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test_jobs.db"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})

from app.models import db_models  # noqa: E402

db_models.Base.metadata.create_all(bind=engine)
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


from app.services.job_store import STAGES, job_store  # noqa: E402

job_store.configure_session(_factory)


def test_lifecycle_claim_stage_complete():
    jid = job_store.create(params={"a": 1})
    job = job_store.get(jid)
    assert job["status"] == "queued"
    assert set(job["stages"]) == set(STAGES)
    assert all(s["status"] == "pending" for s in job["stages"].values())
    assert isinstance(job["version"], int)

    assert job_store.claim(jid) is True
    assert job_store.claim(jid) is False  # already running
    job_store.start_stage(jid, "input", "f.tif")
    job_store.finish_stage(jid, "input")
    job = job_store.get(jid)
    assert job["stages"]["input"]["status"] == "done"
    assert job["stages"]["input"]["started_at"].endswith("Z")
    assert "duration_seconds" in job["stages"]["input"]
    # geospatial lifecycle: start must exist (M3 regression for missing start)
    job_store.start_stage(jid, "geospatial")
    job_store.finish_stage(jid, "geospatial")
    assert job_store.get(jid)["stages"]["geospatial"]["status"] == "done"
    job_store.complete(jid, {"spill_id": "OS-12345678"})
    job = job_store.get(jid)
    assert job["status"] == "done"
    assert job["investigation_id"] == "OS-12345678"
    assert job["result"]["spill_id"] == "OS-12345678"


def test_idempotency_no_duplicates():
    j1 = job_store.create(params={"x": 1}, idempotency_key="idem-001")
    j2 = job_store.create(params={"x": 1}, idempotency_key="idem-001")
    assert j1 == j2
    found = job_store.find_by_idempotency_key("idem-001")
    assert found["job_id"] == j1


def test_cancel_and_mark():
    jid = job_store.create()
    job_store.claim(jid)
    assert job_store.is_cancelled(jid) is False
    assert job_store.request_cancel(jid) is True
    assert job_store.is_cancelled(jid) is True
    job_store.mark_cancelled(jid)
    assert job_store.get(jid)["status"] == "cancelled"
    assert job_store.request_cancel(jid) is False  # terminal


def test_retry_failed_job():
    jid = job_store.create()
    job_store.claim(jid)
    job_store.fail_stage(jid, "drift", "boom")
    assert job_store.get(jid)["status"] == "error"
    assert job_store.retry(jid) is True
    job = job_store.get(jid)
    assert job["status"] == "queued"
    assert all(s["status"] == "pending" for s in job["stages"].values())
    assert job["attempts"] == 1  # attempts preserved, counted at next claim
    assert job_store.retry(jid) is False  # not terminal-failed anymore
    # done jobs cannot be retried
    jid2 = job_store.create()
    job_store.claim(jid2)
    job_store.complete(jid2, {})
    assert job_store.retry(jid2) is False


def test_recover_interrupted_and_retention():
    jid = job_store.create()
    assert job_store.claim(jid) is True
    assert job_store.recover_interrupted() == 1
    assert job_store.get(jid)["status"] == "queued"
    assert job_store.recover_interrupted() == 0
    # age the job past retention and prune
    jid2 = job_store.create()
    job_store.claim(jid2)
    job_store.complete(jid2, {})
    with _factory() as db:
        row = db.query(db_models.Job).filter(db_models.Job.id == jid2).first()
        row.finished_at = datetime.now(timezone.utc) - timedelta(days=400)
    assert job_store.cleanup_old() >= 1
    assert job_store.get(jid2) is None
