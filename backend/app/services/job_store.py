"""Durable DB-backed job store (M3).

Replaces the former in-memory dictionary: jobs and per-stage lifecycle are
persisted rows, so application restarts during a running job leave recoverable
state instead of total loss. Same method names as before so pipeline/SSE
endpoints change minimally.

Status vocabulary:
  job:    queued | running | done | error | cancelled
  stage:  pending | active | done | error | skipped | degraded

Execution is still dispatched via BackgroundTasks (M3 scope = durable state;
CPU/worker isolation is M4). A restart-recovery sweep requeues interrupted
jobs; a retention sweep prunes terminal job rows (spill data is untouched).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

from app.observability import metrics

STAGES = ["input", "detection", "geospatial", "drift", "ais", "scoring", "evidence", "save"]

TERMINAL = ("done", "error", "cancelled")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _loads(text: str | None, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _json_default(obj):
    """Canonical JSON fallback: datetimes serialize as UTC ISO-8601 Z.

    json.dumps(..., default=_json_default) produced space-separated '+00:00' strings
    for any datetime that slipped into params/partials/results, violating the
    canonical timestamp contract.
    """
    from datetime import datetime as _dt

    from app.contracts import to_iso_z

    if isinstance(obj, _dt):
        return to_iso_z(obj)
    return str(obj)


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session_factory: Optional[Callable[[], Iterator]] = None

    # -- test seam ---------------------------------------------------------
    def configure_session(self, factory: Callable[[], Iterator] | None) -> None:
        """Inject a session context-manager factory (tests). None = default."""
        self._session_factory = factory

    @contextmanager
    def _session(self):
        if self._session_factory is not None:
            with self._session_factory() as db:
                yield db
            return
        from app.db import session_scope

        with session_scope() as db:
            yield db

    # -- writes ------------------------------------------------------------
    def create(
        self,
        params: dict | None = None,
        idempotency_key: str | None = None,
        upload_relpath: str | None = None,
        filename: str | None = None,
    ) -> str:
        from app.config import settings
        from app.models.db_models import Job, JobStage

        key = (idempotency_key or "").strip() or None
        if key:
            existing = self.find_by_idempotency_key(key)
            if existing:
                return existing["job_id"]
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        safe_params = dict(params or {})
        with self._session() as db:
            db.add(Job(
                id=job_id,
                idempotency_key=key,
                status="queued",
                params_json=json.dumps(safe_params, default=_json_default),
                upload_relpath=upload_relpath,
                filename=filename,
                partial_json=json.dumps({}),
                attempts=0,
                max_attempts=settings.JOB_MAX_ATTEMPTS,
                version=0,
            ))
            for seq, stage in enumerate(STAGES):
                db.add(JobStage(job_id=job_id, stage=stage, seq=seq, status="pending"))
        return job_id

    def find_by_idempotency_key(self, key: str) -> Optional[dict[str, Any]]:
        from app.models.db_models import Job

        with self._session() as db:
            row = db.query(Job).filter(Job.idempotency_key == key).first()
            return self._to_dict(db, row) if row else None

    def _get_row(self, db, job_id: str):
        from app.models.db_models import Job

        return db.query(Job).filter(Job.id == job_id).first()

    def _touch(self, row, status: str | None = None, stage: str | None = None) -> None:
        row.version = (row.version or 0) + 1
        row.updated_at = _utcnow()
        if status is not None:
            row.status = status
        if stage is not None:
            row.stage = stage

    def claim(self, job_id: str) -> bool:
        """queued -> running (worker pickup). Returns False if not claimable."""
        from app.models.db_models import JobStage

        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row or row.status != "queued":
                    return False
                if (row.attempts or 0) >= (row.max_attempts or 1):
                    row.status = "error"
                    row.error = "Job exceeded max attempts."
                    row.finished_at = _utcnow()
                    self._touch(row)
                    return False
                row.attempts = (row.attempts or 0) + 1
                row.status = "running"
                row.started_at = row.started_at or _utcnow()
                attempt = row.attempts
                for s in db.query(JobStage).filter(JobStage.job_id == job_id).all():
                    s.attempt = attempt
                self._touch(row)
                return True

    def start_stage(self, job_id: str, stage: str, detail: str | None = None) -> None:
        from app.models.db_models import JobStage

        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row:
                    return
                if row.status == "queued":
                    row.status = "running"
                    row.started_at = row.started_at or _utcnow()
                row.stage = stage
                s = db.query(JobStage).filter(JobStage.job_id == job_id, JobStage.stage == stage).first()
                if s:
                    s.status = "active"
                    s.started_at = _utcnow()
                    s.finished_at = None
                    s.duration_ms = None
                    if detail:
                        s.detail = detail
                self._touch(row)

    def finish_stage(self, job_id: str, stage: str, detail: str | None = None, partial: dict | None = None) -> None:
        from app.models.db_models import JobStage

        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row:
                    return
                now = _utcnow()
                s = db.query(JobStage).filter(JobStage.job_id == job_id, JobStage.stage == stage).first()
                if s:
                    s.status = "done"
                    s.finished_at = now
                    if s.started_at:
                        base = s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=timezone.utc)
                        s.duration_ms = int((now - base).total_seconds() * 1000)
                        metrics.observe(f"stage_duration_ms:{stage}", s.duration_ms)
                    if detail:
                        s.detail = detail
                    if partial:
                        try:
                            s.partial_json = json.dumps(partial, default=_json_default)
                        except Exception:
                            pass
                if partial and row.partial_json is not None:
                    merged = _loads(row.partial_json, {})
                    merged.update(partial)
                    row.partial_json = json.dumps(merged, default=_json_default)
                self._touch(row)

    def fail_stage(self, job_id: str, stage: str, message: str) -> None:
        from app.models.db_models import JobStage

        safe = (message or "Stage failed.")[:2000]
        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row:
                    return
                s = db.query(JobStage).filter(JobStage.job_id == job_id, JobStage.stage == stage).first()
                if s:
                    s.status = "error"
                    s.finished_at = _utcnow()
                    if s.started_at and s.finished_at:
                        base = s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=timezone.utc)
                        s.duration_ms = int((s.finished_at - base).total_seconds() * 1000)
                    s.error = safe
                    s.detail = safe
                row.status = "error"
                row.error = safe
                row.finished_at = _utcnow()
                metrics.incr("job_error_total")
                self._touch(row)

    def degrade_stage(self, job_id: str, stage: str, detail: str | None = None, partial: dict | None = None) -> None:
        """M6: mark a stage degraded — output produced but quality gate failed.

        The job keeps running (unlike fail_stage); the stage is never shown
        as completed."""
        from app.models.db_models import JobStage

        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row:
                    return
                now = _utcnow()
                s = db.query(JobStage).filter(JobStage.job_id == job_id, JobStage.stage == stage).first()
                if s:
                    s.status = "degraded"
                    s.finished_at = now
                    metrics.incr("stage_degraded_total")
                    if s.started_at:
                        base = s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=timezone.utc)
                        s.duration_ms = int((now - base).total_seconds() * 1000)
                    if detail:
                        s.detail = detail
                    if partial:
                        try:
                            s.partial_json = json.dumps(partial, default=_json_default)
                        except Exception:
                            pass
                if partial and row.partial_json is not None:
                    merged = _loads(row.partial_json, {})
                    merged.update(partial)
                    row.partial_json = json.dumps(merged, default=_json_default)
                self._touch(row)

    def complete(self, job_id: str, result: dict) -> None:
        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row:
                    return
                try:
                    row.result_json = json.dumps(result, default=_json_default)
                except Exception:
                    row.result_json = "{}"
                spill_id = None
                if isinstance(result, dict):
                    spill_id = result.get("spill_id")
                if spill_id:
                    row.investigation_id = spill_id
                row.status = "done"
                row.stage = None
                row.finished_at = _utcnow()
                metrics.incr("job_done_total")
                self._touch(row)

    def fail(self, job_id: str, message: str) -> None:
        safe = (message or "Job failed.")[:2000]
        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row:
                    return
                row.status = "error"
                row.error = safe
                row.finished_at = _utcnow()
                metrics.incr("job_error_total")
                self._touch(row)

    # -- cancellation / retry ----------------------------------------------
    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row or row.status in TERMINAL:
                    return False
                row.cancel_requested = True
                self._touch(row)
                return True

    def is_cancelled(self, job_id: str | None) -> bool:
        if not job_id:
            return False
        with self._session() as db:
            row = self._get_row(db, job_id)
            return bool(row and row.cancel_requested)

    def mark_cancelled(self, job_id: str) -> None:
        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row:
                    return
                row.status = "cancelled"
                row.error = "Cancelled by user."
                row.finished_at = _utcnow()
                metrics.incr("job_cancelled_total")
                self._touch(row)

    def retry(self, job_id: str) -> bool:
        """Reset a terminal failed/error job to queued. Uploads are retained
        under data/jobs/<job_id>/ so upload-based jobs can actually re-run."""
        from app.models.db_models import JobStage

        with self._lock:
            with self._session() as db:
                row = self._get_row(db, job_id)
                if not row or row.status not in ("error",):
                    return False
                if (row.attempts or 0) >= (row.max_attempts or 1):
                    return False
                row.status = "queued"
                row.stage = None
                row.error = None
                row.result_json = None
                row.partial_json = json.dumps({})
                row.cancel_requested = False
                row.started_at = None
                row.finished_at = None
                for s in db.query(JobStage).filter(JobStage.job_id == job_id).all():
                    s.status = "pending"
                    s.detail = None
                    s.error = None
                    s.partial_json = None
                    s.started_at = None
                    s.finished_at = None
                    s.duration_ms = None
                self._touch(row)
                return True

    # -- reads ---------------------------------------------------------------
    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._session() as db:
            return self._to_dict(db, self._get_row(db, job_id))

    def _to_dict(self, db, row) -> Optional[dict[str, Any]]:
        from app.models.db_models import JobStage

        if row is None:
            return None
        stages = db.query(JobStage).filter(JobStage.job_id == row.id).order_by(JobStage.seq).all()
        out_stages: dict[str, dict[str, Any]] = {}
        for s in stages:
            entry: dict[str, Any] = {
                "status": s.status,
                "started_at": _iso(s.started_at),
                "finished_at": _iso(s.finished_at),
                "detail": s.detail,
            }
            if s.duration_ms is not None:
                entry["duration_seconds"] = round(s.duration_ms / 1000.0, 2)
            out_stages[s.stage] = entry
        for name in STAGES:
            out_stages.setdefault(name, {"status": "pending", "started_at": None, "finished_at": None, "detail": None})
        return {
            "job_id": row.id,
            "status": row.status,
            "stage": row.stage,
            "stages": out_stages,
            "partial": _loads(row.partial_json, {}),
            "result": _loads(row.result_json, None),
            "error": row.error,
            "attempts": row.attempts or 0,
            "max_attempts": row.max_attempts or 0,
            "cancel_requested": bool(row.cancel_requested),
            "investigation_id": row.investigation_id,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "version": row.version or 0,
        }

    # -- maintenance -----------------------------------------------------------
    def recover_interrupted(self) -> int:
        """Startup sweep: running jobs from a crashed process go back to queued
        (attempt counted at next claim); queued jobs are left alone. Returns
        the number of jobs requeued."""
        from app.models.db_models import Job

        count = 0
        with self._lock:
            with self._session() as db:
                for row in db.query(Job).filter(Job.status == "running").all():
                    row.status = "queued"
                    row.stage = None
                    row.error = "Interrupted by restart; requeued."
                    row.updated_at = _utcnow()
                    count += 1
        if count:
            logger.warning("JOB RECOVERY | requeued %d interrupted job(s)", count)
        return count

    def cleanup_old(self) -> int:
        """Prune terminal job rows (and their retained upload dirs) older than
        JOB_RETENTION_DAYS. Spill/investigation data is never touched."""
        import shutil
        from pathlib import Path

        from app.config import settings
        from app.models.db_models import Job

        cutoff = _utcnow() - timedelta(days=settings.JOB_RETENTION_DAYS)
        pruned = 0
        with self._lock:
            with self._session() as db:
                rows = db.query(Job).filter(
                    Job.status.in_(list(TERMINAL)),
                    Job.finished_at.isnot(None),
                    Job.finished_at < cutoff,
                ).all()
                ids = [r.id for r in rows]
                for r in rows:
                    db.delete(r)
                pruned = len(ids)
        for job_id in ids:
            try:
                shutil.rmtree(str(Path(settings.DATA_DIR) / "jobs" / job_id), ignore_errors=True)
            except Exception:
                pass
        if pruned:
            logger.info("JOB RETENTION | pruned %d job(s) older than %d days", pruned, settings.JOB_RETENTION_DAYS)
        return pruned


job_store = JobStore()
