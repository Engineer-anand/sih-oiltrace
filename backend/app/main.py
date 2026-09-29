"""
OilTrace backend — full end-to-end entrypoint.

Run with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Then open http://localhost:8000/docs for the interactive Swagger UI, or
open frontend/index.html for the dashboard.
"""

from __future__ import annotations

import logging
import os

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.ais import router as ais_router
from app.api.detection import router as detection_router
from app.api.drift import router as drift_router
from app.api.evidence import router as evidence_router
from app.api.pipeline import router as pipeline_router
from app.api.reports import router as reports_router
from app.api.sentinel1 import router as sentinel1_router
from app.config import settings, validate_app_mode
from app.api.scoring import router as scoring_router
from app.db import init_db, engine
from app.security import rate_limiter
from app.observability import metrics, new_request_id, request_id_ctx, setup as observability_setup

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="OilTrace API",
    description=(
        "End-to-end oil spill detection & attribution system: SAR detection, "
        "geospatial extraction, backward drift simulation, AIS vessel matching, "
        "attribution scoring, and explainability — all in one API."
    ),
    version="1.0.0",
 )

@app.middleware("http")
async def request_logging_middleware(request, call_next):
    import re as _re

    request_id = request.headers.get("X-Request-ID") or new_request_id()
    request_id_ctx.set(request_id)
    started = __import__("time").perf_counter()
    if not request.url.path.startswith(("/health", "/app", "/sat", "/assets", "/docs", "/openapi.json")):
        try:
            rate_limiter.check(request)
        except Exception as exc:
            from fastapi.responses import JSONResponse
            status = getattr(exc, "status_code", 429)
            return JSONResponse(status_code=status, content={"type": "rate-limited", "title": "Rate limit exceeded", "status": status})
    try:
        response = await call_next(request)
        elapsed = (__import__("time").perf_counter() - started) * 1000
        route = _re.sub(r"OS-[A-F0-9]{8}", "OS-ID", _re.sub(r"job_[a-f0-9]{12}", "job-ID", request.url.path))
        metrics.observe(f"api_latency:{route}", elapsed)
        response.headers["X-Request-ID"] = request_id
        if response.status_code >= 400:
            logger.error("HTTP ERROR %s %s -> %s | %.1fms", request.method, request.url.path, response.status_code, elapsed)
        else:
            logger.info("HTTP %s %s -> %s | %.1fms", request.method, request.url.path, response.status_code, elapsed)
        return response
    except Exception:
        elapsed = (__import__("time").perf_counter() - started) * 1000
        logger.exception("HTTP ERROR %s %s | %.1fms", request.method, request.url.path, elapsed)
        raise

# CORS: explicit origins per environment. Refuse wildcard outside development.
_cors_raw = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://localhost:5173")
cors_origins = [x.strip() for x in _cors_raw.split(",") if x.strip()]
if settings.APP_MODE in ("staging", "production") and "*" in cors_origins:
    raise RuntimeError("Refusing to start: CORS_ORIGINS='*' is forbidden in staging/production.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(detection_router)
app.include_router(drift_router)
app.include_router(ais_router)
app.include_router(scoring_router)
app.include_router(evidence_router)
app.include_router(pipeline_router)
app.include_router(reports_router)
app.include_router(sentinel1_router)

class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and scope.get("method") == "GET" and not path.startswith("assets/"):
                return FileResponse(str(Path(self.directory) / "index.html"))
            raise


_frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
_frontend_dist = _frontend_dir / "dist"
_frontend_index = _frontend_dist / "index.html"

if _frontend_dist.exists():
    @app.get("/", include_in_schema=False)
    def root_redirect():
        return RedirectResponse("/app/", status_code=307)

    @app.get("/app", include_in_schema=False)
    def frontend_root_redirect():
        return RedirectResponse("/app/", status_code=307)

    if (_frontend_dist / "sat").exists():
        app.mount("/sat", StaticFiles(directory=str(_frontend_dist / "sat")), name="sat-root")

    if (_frontend_dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="assets-root")

    @app.get("/app/", include_in_schema=False)
    @app.get("/app/{spa_path:path}", include_in_schema=False)
    def frontend_spa(spa_path: str = ""):
        if spa_path:
            candidate = (_frontend_dist / spa_path).resolve()
            root = _frontend_dist.resolve()
            # Containment check: never read outside the build directory.
            if candidate.is_file() and root in candidate.parents:
                return FileResponse(str(candidate))
        return FileResponse(str(_frontend_index))
else:
    # Honest status page. The divergent CDN fallback UI was retired (M1): it
    # shipped development React builds and a latent path-traversal route.
    @app.get("/app", include_in_schema=False)
    @app.get("/app/", include_in_schema=False)
    def frontend_unbuilt():
        logger.warning("FRONTEND UNAVAILABLE | Vite dist not built; serving status page")
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<html><body><h1>OilTrace</h1><p>Dashboard bundle unavailable. API is healthy at /health and /docs.</p></body></html>")


@app.on_event("startup")
def on_startup() -> None:
    validate_app_mode()
    observability_setup()
    init_db()
    try:
        from app.services.job_store import job_store

        job_store.recover_interrupted()
        job_store.cleanup_old()
    except Exception:
        logger.exception("JOB STARTUP SWEEP FAILED")
    try:
        from app.services.storage import sweep_temp_files

        sweep_temp_files()
    except Exception:
        logger.exception("TEMP STARTUP SWEEP FAILED")
    # Free-tier deploy safety: resolve the model BEFORE the server starts
    # accepting connections. Uvicorn only begins serving once startup handlers
    # return, so the first platform health check finds a warm singleton instead
    # of paying a multi-second cold model load that could fail the deploy.
    # Non-fatal by design: a missing/broken checkpoint must still leave the API
    # up so /health can report "degraded" honestly rather than crash-looping.
    if settings.MODEL_PRELOAD:
        try:
            from app.services.inference import get_detector

            get_detector()
            logger.info("MODEL PRELOAD | trained U-Net resident before first request")
        except Exception:
            logger.exception("MODEL PRELOAD FAILED | /health will report degraded")
    logger.info("Database initialized | backend=%s", engine.url.get_backend_name())
    logger.info("OilTrace startup complete | LIVE_FIRST=%s | FALLBACK_ON_FAILURE=%s | MODEL=TRAINED_ONLY | Sentinel1=%s", settings.REALTIME_DATA_ONLY, settings.ALLOW_LIVE_FALLBACK, settings.AWS_SENTINEL1_COLLECTION)


@app.get("/health", tags=["meta"])
def health_check() -> dict:
    from app.config import settings
    from app.services.inference import get_detector

    try:
        detector = get_detector()
    except Exception:
        logger.exception("HEALTH ERROR | trained U-Net unavailable")
        return {
            "status": "degraded",
            "realtime_data_only": settings.REALTIME_DATA_ONLY,
            "fallback_on_api_failure": settings.ALLOW_LIVE_FALLBACK,
            "model_loaded": False,
            "mode": "blocked_no_trained_model",
            "app_mode": settings.APP_MODE,
        }
    return {
        "status": "ok",
        "realtime_data_only": settings.REALTIME_DATA_ONLY,
        "fallback_on_api_failure": settings.ALLOW_LIVE_FALLBACK,
        "model_loaded": detector.model is not None,
        "mode": "trained_unet_only",
        "app_mode": settings.APP_MODE,
    }


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "OilTrace API",
        "docs": "/docs",
        "endpoints": {
            "detect": "POST /api/v1/detect",
            "drift": "POST /api/v1/drift/simulate",
            "ais": "POST /api/v1/ais/search",
            "score": "POST /api/v1/scores/compute",
            "evidence": "POST /api/v1/evidence/build",
            "full_pipeline": "POST /api/v1/pipeline/run",
            "list_spills": "GET /api/v1/spills",
            "spill_detail": "GET /api/v1/spills/{id}",
            "export": "GET /api/v1/export/{id}?fmt=geojson|csv",
            "report": "GET /api/v1/report/{id}",
            "sentinel1_search": "GET /api/v1/sentinel1/search",
            "sentinel1_scene": "GET /api/v1/sentinel1/scene/{scene_id}",
            "sentinel1_download": "POST /api/v1/sentinel1/download",
            "trajectory": "GET /api/v1/spills/{id}/trajectory",
            "db_status": "GET /api/v1/db/status",
        },
    }


@app.get("/api/v1/db/status", tags=["meta"])
def database_status() -> dict:
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            is_pg = engine.url.get_backend_name() == "postgresql"
            postgis = False
            if is_pg:
                postgis = bool(conn.execute(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='postgis')")).scalar())
        return {"status":"ok","backend":engine.url.get_backend_name(),"postgis":postgis}
    except Exception:
        logger.exception("Database status check failed")
        return {"status":"error","backend":engine.url.get_backend_name(),"postgis":False}


@app.get("/metrics", tags=["meta"])
def metrics_snapshot() -> dict:
    """M15: in-memory metrics snapshot (counters + latency percentiles)."""
    snap = metrics.snapshot()
    snap["synthetic_fallback_total"] = snap["counters"].get("synthetic_fallback_total", 0)
    return snap


@app.get("/api/v1/health/ready", tags=["meta"])
def readiness() -> dict:
    """M15: readiness with per-dependency status (liveness stays at /health).

    Reports credential *presence* only — never values.
    """
    from sqlalchemy import text

    from app.observability import check_readiness

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    try:
        from app.services.inference import get_detector

        get_detector()
        model_ok = True
    except Exception:
        model_ok = False
    try:
        probe = settings.ARTIFACTS_DIR / ".writetest"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        storage_ok = True
    except Exception:
        storage_ok = False
    providers = {
        "cmems": {"configured": bool(settings.CMEMS_USERNAME and settings.CMEMS_PASSWORD)},
        "cds": {"configured": bool(settings.CDSAPI_KEY)},
        "gfw": {"configured": bool(settings.GFW_API_TOKEN)},
    }
    # In sih_demo the drift and AIS stages deliberately use labelled SCENARIO
    # substitutes, so absent provider credentials are the expected, correct
    # state — not a fault. Reporting them as "not configured" made the demo
    # look broken to anyone reading readiness.
    scenario_expected = settings.APP_MODE == "sih_demo"
    result = check_readiness(db_ok, model_ok, storage_ok, providers)
    if scenario_expected:
        result["app_mode"] = settings.APP_MODE
        result["provider_requirement"] = "not-required-in-sih_demo"
        result["scenario_substitution"] = ["drift_environmental_fields", "ais_positions"]
    return result
