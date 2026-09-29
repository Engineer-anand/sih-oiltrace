"""M14 storage tests — local backend round-trip, traversal guard, S3 closed."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

_TMPDB = tempfile.mkdtemp(prefix="oiltrace_storage_test_")
# Must precede the db_models import: selects the portable TEXT geometry.
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDB}/test_storage.db"

from app.services import storage as st  # noqa: E402


def test_local_roundtrip_and_checksum():
    root = tempfile.mkdtemp(prefix="st_test_")
    backend = st.LocalStorage(root=root)
    data = b'{"type":"FeatureCollection"}'
    stored = backend.put("OS-ABCDEF12", "export-geojson", 1, "OS-ABCDEF12.geojson", data)
    assert stored.uri.startswith("local://")
    assert stored.version == 1 and stored.size_bytes == len(data)
    import hashlib

    assert stored.checksum == hashlib.sha256(data).hexdigest()
    assert backend.get(stored.uri) == data


def test_local_traversal_guard():
    root = tempfile.mkdtemp(prefix="st_trav_")
    backend = st.LocalStorage(root=root)
    try:
        backend.get("local://../evil.bin")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        backend.get("s3://bucket/key")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_next_version():
    assert st.next_version([]) == 1
    assert st.next_version([1, 2, 5]) == 6


def test_s3_fails_closed_without_dependency_or_bucket():
    try:
        import boto3  # noqa: F401
        print("SKIP s3-closed test (boto3 installed)")
        return
    except ImportError:
        pass
    try:
        st.S3Storage(bucket="x")
        raise AssertionError("expected RuntimeError without boto3")
    except RuntimeError as exc:
        assert "boto3" in str(exc)


def test_record_artifact_persists_row():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import db_models

    tmp = tempfile.mkdtemp(prefix="st_row_")
    eng = create_engine(f"sqlite:///{tmp}/art.db", connect_args={"check_same_thread": False})
    db_models.Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    db = Session()
    try:
        root = tempfile.mkdtemp(prefix="st_art_")
        import app.services.storage as stm

        old = stm.settings.ARTIFACTS_DIR
        stm.settings.ARTIFACTS_DIR = root
        try:
            rec = stm.record_artifact(db, "OS-12345678", "report-pdf", "r.pdf", b"%PDF-1.4")
            assert rec and rec["version"] == 1
            rec2 = stm.record_artifact(db, "OS-12345678", "report-pdf", "r.pdf", b"%PDF-1.5")
            assert rec2["version"] == 2
            rows = db.query(db_models.ReportArtifact).all()
            assert len(rows) == 2
            assert rows[0].checksum != rows[1].checksum
        finally:
            stm.settings.ARTIFACTS_DIR = old
    finally:
        db.close()
