"""M18 production-hardening tests — boot validator, sweeps, container/docs."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from app.config import settings, validate_app_mode  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..", "..")


def _snapshot():
    return (settings.APP_MODE, settings.DATABASE_URL, settings.ALLOW_LIVE_FALLBACK,
            settings.OILTRACE_API_KEY, settings.CMEMS_USERNAME, settings.CMEMS_PASSWORD,
            settings.CDSAPI_KEY, settings.GFW_API_TOKEN, settings.WEIGHT_PROXIMITY,
            os.environ.get("CORS_ORIGINS"))


def _restore(snap):
    (settings.APP_MODE, settings.DATABASE_URL, settings.ALLOW_LIVE_FALLBACK,
     settings.OILTRACE_API_KEY, settings.CMEMS_USERNAME, settings.CMEMS_PASSWORD,
     settings.CDSAPI_KEY, settings.GFW_API_TOKEN, settings.WEIGHT_PROXIMITY, cors) = snap
    if cors is None:
        os.environ.pop("CORS_ORIGINS", None)
    else:
        os.environ["CORS_ORIGINS"] = cors


def _production_ready():
    settings.APP_MODE = "production"
    settings.DATABASE_URL = "postgresql+psycopg2://u:p@host/db"
    settings.ALLOW_LIVE_FALLBACK = False
    settings.OILTRACE_API_KEY = "k" * 32
    settings.CMEMS_USERNAME = "u"
    settings.CMEMS_PASSWORD = "p"
    settings.CDSAPI_KEY = "k"
    settings.GFW_API_TOKEN = "t"
    os.environ["CORS_ORIGINS"] = "https://example.com"


def test_production_refuses_missing_credentials():
    snap = _snapshot()
    try:
        _production_ready()
        settings.GFW_API_TOKEN = None
        try:
            validate_app_mode()
            raise AssertionError("expected refusal for missing GFW token")
        except RuntimeError as exc:
            assert "GFW_API_TOKEN" in str(exc)
    finally:
        _restore(snap)


def test_production_passes_when_fully_configured():
    snap = _snapshot()
    try:
        _production_ready()
        validate_app_mode()
    finally:
        _restore(snap)


def test_staging_refuses_allow_live_fallback():
    """Release audit regression: staging must never be able to boot with the
    synthetic-fallback flag (FALLBACK substitution is development-only)."""
    snap = _snapshot()
    try:
        settings.APP_MODE = "staging"
        settings.DATABASE_URL = "postgresql+psycopg2://user:pass@localhost:5432/oiltrace"
        settings.ALLOW_LIVE_FALLBACK = True
        settings.OILTRACE_API_KEY = "test-key"
        old_cors = os.environ.get("CORS_ORIGINS")
        os.environ["CORS_ORIGINS"] = "https://example.com"
        try:
            validate_app_mode()
            raise AssertionError("expected refusal of ALLOW_LIVE_FALLBACK in staging")
        except RuntimeError as exc:
            assert "ALLOW_LIVE_FALLBACK" in str(exc)
        finally:
            if old_cors is None:
                os.environ.pop("CORS_ORIGINS", None)
            else:
                os.environ["CORS_ORIGINS"] = old_cors
    finally:
        _restore(snap)


def test_production_refuses_bad_weights():
    snap = _snapshot()
    try:
        _production_ready()
        settings.WEIGHT_PROXIMITY = 0.9
        try:
            validate_app_mode()
            raise AssertionError("expected refusal for bad weights")
        except RuntimeError as exc:
            assert "weights" in str(exc).lower()
    finally:
        _restore(snap)


def test_sweep_temp_files_only_targets_stale_staging():
    from app.services.storage import sweep_temp_files

    root = tempfile.mkdtemp(prefix="sweep_test_")
    old_tmp = os.path.join(root, "tmpABC123.tif")
    fresh_tmp = os.path.join(root, "tmpFRESH.tif")
    keep = os.path.join(root, "oiltrace.db")
    for p in (old_tmp, fresh_tmp, keep):
        with open(p, "wb") as fh:
            fh.write(b"x")
    ancient = time.time() - 48 * 3600
    os.utime(old_tmp, (ancient, ancient))
    removed = sweep_temp_files(root=root, older_than_hours=24.0)
    assert removed == 1
    assert not os.path.exists(old_tmp)
    assert os.path.exists(fresh_tmp) and os.path.exists(keep)


def test_container_has_no_runtime_data_and_runs_nonroot():
    with open(os.path.join(REPO, "Dockerfile"), encoding="utf-8") as fh:
        docker = fh.read()
    assert "COPY data" not in docker
    # Non-root runtime is enforced by the privilege-dropping entrypoint.
    # A plain `USER oiltrace` image default would boot as oiltrace and
    # crash-loop (mkdir EACCES) whenever the data volume is root-owned
    # from an older image — the entrypoint repairs ownership as root
    # first, then drops to uid 10001 before the server starts.
    assert "USER oiltrace" not in docker
    assert 'ENTRYPOINT ["/entrypoint.sh"]' in docker
    with open(os.path.join(REPO, "docker", "entrypoint.sh"), encoding="utf-8") as fh:
        entrypoint = fh.read()
    assert "setpriv --reuid=10001 --regid=10001 --init-groups" in entrypoint
    assert "HEALTHCHECK" in docker
    with open(os.path.join(REPO, ".dockerignore"), encoding="utf-8") as fh:
        ignore = fh.read()
    assert "data/" in ignore and "*.db" in ignore
    with open(os.path.join(REPO, "docker-compose.yml"), encoding="utf-8") as fh:
        compose = fh.read()
    assert "healthcheck" in compose


def test_render_defaults_are_staging_safe():
    with open(os.path.join(REPO, "render.yaml"), encoding="utf-8") as fh:
        text = fh.read()
    assert "APP_MODE" in text and "staging" in text
    assert 'ALLOW_LIVE_FALLBACK"\n        value: "false"' in text or 'value: "false"' in text


def test_full_app_imports_and_routes_resolve():
    """Regression (release audit): app.main failed to import because
    PipelineRunResponse used Field without importing it. No test imported
    the FastAPI app module, so the server could never boot. Import the whole
    app and enumerate routes to catch this class of failure."""
    import importlib

    main = importlib.import_module("app.main")
    app = main.app
    assert app is not None
    paths = {getattr(r, "path", None) for r in app.routes}
    for required in ("/health", "/metrics", "/api/v1/health/ready",
                     "/api/v1/pipeline/jobs", "/api/v1/spills",
                     "/api/v1/ais/history", "/api/v1/report/{spill_id}"):
        assert required in paths, f"missing route {required}"


def test_settings_model_paths_exist_in_repo_layout():
    """MODELS_DIR and the manifest must resolve to real files, matching the
    Dockerfile COPY layout (models/ -> /app/models)."""
    from app.config import settings

    assert settings.MODELS_DIR.exists()
    assert (settings.MODELS_DIR / "unet_oilspill.pt").exists()
    from app.services.inference import MANIFEST_PATH

    assert MANIFEST_PATH.exists(), "MODEL_VERSION.json missing"
