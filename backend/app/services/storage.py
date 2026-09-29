"""Artifact object storage (M14).

Abstraction with two backends:
- LocalStorage (default, SIH): files under data/artifacts/<spill>/<type>/v<n>/<file>.
- S3Storage (optional production): S3-compatible bucket (AWS S3, MinIO,
  or any S3 API); selected only when OILTRACE_S3_BUCKET is set. boto3 stays
  an optional dependency — never required for SIH.

Every stored artifact returns {uri, checksum, artifact_type, version,
created} for the ReportArtifact row. Large trajectory/particle-cloud
offload is deferred to the scaling milestone (documented, not silently
dropped); reports and exports move now.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class StoredArtifact:
    uri: str
    checksum: str  # sha256 hex
    artifact_type: str
    version: int
    created: str
    size_bytes: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LocalStorage:
    name = "local"

    def __init__(self, root: Path | None = None):
        self.root = Path(root or settings.ARTIFACTS_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, spill_id: str, artifact_type: str, version: int, filename: str) -> Path:
        safe_spill = "".join(c for c in spill_id if c.isalnum() or c in "-_").strip() or "unknown"
        safe_type = "".join(c for c in artifact_type if c.isalnum() or c in "-_").strip() or "misc"
        safe_name = "".join(c for c in filename if c.isalnum() or c in "-_.").strip() or "artifact.bin"
        return self.root / safe_spill / safe_type / f"v{version}" / safe_name

    def put(self, spill_id: str, artifact_type: str, version: int, filename: str, data: bytes) -> StoredArtifact:
        dest = self._path(spill_id, artifact_type, version, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return StoredArtifact(uri=f"local://{dest.relative_to(self.root).as_posix()}",
                              checksum=_sha256(data), artifact_type=artifact_type,
                              version=version, created=_now_z(), size_bytes=len(data))

    def get(self, uri: str) -> bytes:
        if not uri.startswith("local://"):
            raise ValueError(f"LocalStorage cannot read uri: {uri}")
        path = self.root / uri[len("local://"):]
        resolved = path.resolve()
        if self.root.resolve() not in resolved.parents and resolved != self.root.resolve():
            raise ValueError("Artifact path escapes storage root.")
        return resolved.read_bytes()


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str | None = None, prefix: str | None = None, endpoint_url: str | None = None):
        try:
            import boto3  # optional dependency
        except ImportError as exc:
            raise RuntimeError(
                "boto3 is not installed. S3 storage is optional; install boto3 "
                "or unset OILTRACE_S3_BUCKET to use local storage."
            ) from exc
        self.bucket = bucket or settings.OILTRACE_S3_BUCKET
        if not self.bucket:
            raise RuntimeError("OILTRACE_S3_BUCKET is not set.")
        self.prefix = (prefix or settings.OILTRACE_S3_PREFIX).strip("/")
        self._client = boto3.client("s3", endpoint_url=endpoint_url or settings.OILTRACE_S3_ENDPOINT_URL)

    def _key(self, spill_id: str, artifact_type: str, version: int, filename: str) -> str:
        return f"{self.prefix}/{spill_id}/{artifact_type}/v{version}/{filename}"

    def put(self, spill_id: str, artifact_type: str, version: int, filename: str, data: bytes) -> StoredArtifact:
        key = self._key(spill_id, artifact_type, version, filename)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                ChecksumSHA256=__import__("base64").b64encode(bytes.fromhex(_sha256(data))).decode())
        return StoredArtifact(uri=f"s3://{self.bucket}/{key}", checksum=_sha256(data),
                              artifact_type=artifact_type, version=version,
                              created=_now_z(), size_bytes=len(data))

    def get(self, uri: str) -> bytes:
        assert uri.startswith("s3://")
        rest = uri[len("s3://"):]
        bucket, _, key = rest.partition("/")
        obj = self._client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()


def get_storage():
    """Select backend by configuration: S3 when a bucket is set, else local."""
    if settings.OILTRACE_S3_BUCKET:
        return S3Storage()
    return LocalStorage()


def next_version(existing_versions: list[int]) -> int:
    return (max(existing_versions) if existing_versions else 0) + 1


def sweep_temp_files(root: Path | None = None, older_than_hours: float = 24.0) -> int:
    """M18: remove orphaned upload staging files (tmp*) left by crashes.

    Only targets the random staging names produced by NamedTemporaryFile in
    the data root — never job dirs, caches, or the database.
    """
    import time as _time

    base = Path(root or settings.DATA_DIR)
    cutoff = _time.time() - older_than_hours * 3600.0
    removed = 0
    try:
        entries = list(base.iterdir())
    except OSError:
        return 0
    for path in entries:
        try:
            if not path.is_file() or not path.name.startswith("tmp"):
                continue
            if path.stat().st_mtime > cutoff:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info("TEMP SWEEP | removed %d orphaned staging file(s)", removed)
    return removed


def record_artifact(db, spill_id: str, artifact_type: str, filename: str, data: bytes) -> dict | None:
    """Best-effort artifact persistence (M14): store bytes, record row.

    Never raises — storage failure is logged and serving continues, so a
    storage outage cannot break report/export delivery.
    """
    try:
        from app.models.db_models import ReportArtifact

        versions = [r.version for r in db.query(ReportArtifact).filter(
            ReportArtifact.spill_id == spill_id,
            ReportArtifact.artifact_type == artifact_type).all()]
        version = next_version([v or 0 for v in versions])
        stored = get_storage().put(spill_id, artifact_type, version, filename, data)
        db.add(ReportArtifact(spill_id=spill_id, artifact_type=artifact_type,
                              uri=stored.uri, checksum=stored.checksum,
                              version=version, size_bytes=stored.size_bytes))
        db.commit()
        return {"uri": stored.uri, "checksum": stored.checksum, "version": version}
    except Exception:
        logger.exception("ARTIFACT PERSIST FAILED | spill=%s | type=%s", spill_id, artifact_type)
        try:
            from app.observability import metrics as _metrics

            _metrics.incr("storage_failure_total")
        except Exception:
            pass
        try:
            db.rollback()
        except Exception:
            pass
        return None
