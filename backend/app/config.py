"""Central configuration for OilTrace."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1","true","yes","on"}

def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))

def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))

class Settings:
    APP_MODE: str = os.getenv("APP_MODE", "development").strip().lower()
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+psycopg2://oiltrace:oiltrace@localhost:5432/oiltrace")

    OILTRACE_API_KEY: str | None = os.getenv("OILTRACE_API_KEY") or None
    RATE_LIMIT_PER_MINUTE: int = env_int("RATE_LIMIT_PER_MINUTE", 60)
    SSRF_ALLOWED_HOSTS: tuple[str, ...] = tuple(
        h.strip().lower() for h in os.getenv(
            "SSRF_ALLOWED_HOSTS",
            "earth-search.aws.element84.com,sentinel-s1-l1c.s3.eu-central-1.amazonaws.com",
        ).split(",") if h.strip()
    )

    CMEMS_USERNAME: str | None = os.getenv("CMEMS_USERNAME") or None
    CMEMS_PASSWORD: str | None = os.getenv("CMEMS_PASSWORD") or None
    CDSAPI_URL: str = os.getenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
    CDSAPI_KEY: str | None = os.getenv("CDSAPI_KEY") or None

    AWS_SENTINEL1_STAC_URL: str = os.getenv("AWS_SENTINEL1_STAC_URL", "https://earth-search.aws.element84.com/v1")
    AWS_SENTINEL1_COLLECTION: str = os.getenv("AWS_SENTINEL1_COLLECTION", "sentinel-1-grd")
    AWS_SENTINEL1_BUCKET: str = os.getenv("AWS_SENTINEL1_BUCKET", "sentinel-s1-l1c")
    AWS_SENTINEL1_REGION: str = os.getenv("AWS_SENTINEL1_REGION", "eu-central-1")
    GFW_API_TOKEN: str | None = os.getenv("GFW_API_TOKEN") or None

    ALLOW_LIVE_FALLBACK: bool = env_bool("ALLOW_LIVE_FALLBACK", True)
    REALTIME_DATA_ONLY: bool = env_bool("REALTIME_DATA_ONLY", True)
    REQUIRE_REAL_MODEL: bool = env_bool("REQUIRE_REAL_MODEL", True)
    FAST_PROTOTYPE_MODE: bool = env_bool("FAST_PROTOTYPE_MODE", True)
    PIPELINE_TARGET_SECONDS: int = env_int("PIPELINE_TARGET_SECONDS", 30)

    DEFAULT_LOOKBACK_HOURS: int = env_int("DEFAULT_LOOKBACK_HOURS", 12)
    DRIFT_PARTICLES: int = env_int("DRIFT_PARTICLES", 48)
    DRIFT_OUTPUT_INTERVAL_HOURS: int = env_int("DRIFT_OUTPUT_INTERVAL_HOURS", 2)
    DRIFT_ENV_PAD_DEG: float = env_float("DRIFT_ENV_PAD_DEG", 0.75)
    INFERENCE_MAX_PIXELS: int = env_int("INFERENCE_MAX_PIXELS", 1_000_000)
    INFERENCE_TILE_SIZE: int = env_int("INFERENCE_TILE_SIZE", 256)
    INFERENCE_TILE_OVERLAP: int = env_int("INFERENCE_TILE_OVERLAP", 32)
    # Tiles per forward pass. 16 is fine on a workstation; on a 0.1 vCPU /
    # 512 MB free instance 4 keeps peak activation memory flat.
    INFERENCE_TILE_BATCH: int = env_int("INFERENCE_TILE_BATCH", 16)
    # TorchScript tracing costs startup time and a second copy of the graph.
    # Worth it on a long-lived production instance, wasteful on free tier.
    TORCHSCRIPT_TRACE: bool = env_bool("TORCHSCRIPT_TRACE", True)
    # Eagerly resolve the model at startup so the platform health check never
    # pays the first-load cost (and cannot fail the deploy).
    MODEL_PRELOAD: bool = env_bool("MODEL_PRELOAD", True)
    # 0 = leave PyTorch's default intra-op pool alone (workstation behaviour).
    # Free/shared instances should set 1: a wide thread pool on a fractional
    # vCPU only adds contention and RSS.
    TORCH_NUM_THREADS: int = env_int("TORCH_NUM_THREADS", 0)
    MAX_UPLOAD_BYTES: int = env_int("MAX_UPLOAD_BYTES", 1024 * 1024 * 1024)

    JOB_MAX_ATTEMPTS: int = env_int("JOB_MAX_ATTEMPTS", 3)
    JOB_RETENTION_DAYS: int = env_int("JOB_RETENTION_DAYS", 30)
    WORKER_CONCURRENCY: int = env_int("WORKER_CONCURRENCY", 2)
    JOB_TIMEOUT_S: int = env_int("JOB_TIMEOUT_S", 1800)
    ENV_CACHE_TTL_HOURS: int = env_int("ENV_CACHE_TTL_HOURS", 168)
    ENV_FRESH_HOURS: int = env_int("ENV_FRESH_HOURS", 72)
    DRIFT_SEED: int = env_int("DRIFT_SEED", 20260905)
    DRIFT_QUALITY_MIN: float = env_float("DRIFT_QUALITY_MIN", 60.0)
    TRACK_GAP_HOURS: float = env_float("TRACK_GAP_HOURS", 6.0)
    AIS_STALE_HOURS: float = env_float("AIS_STALE_HOURS", 72.0)

    # Allow the model artefacts and runtime data to be relocated (mounted
    # volume, object-storage staging area, read-only image with a writable
    # /data mount). Defaults are byte-for-byte the previous values, so the
    # repository layout and the existing layout assertions still hold.
    MODELS_DIR: Path = Path(os.getenv("OILTRACE_MODELS_DIR") or (PROJECT_ROOT / "models" / "checkpoints"))
    DATA_DIR: Path = Path(os.getenv("OILTRACE_DATA_DIR") or (PROJECT_ROOT / "data"))
    OCEAN_CACHE_DIR: Path = DATA_DIR / "ocean_cache"
    UPLOADS_DIR: Path = DATA_DIR / "uploads"
    SENTINEL1_DIR: Path = DATA_DIR / "sentinel1"
    ARTIFACTS_DIR: Path = DATA_DIR / "artifacts"
    OILTRACE_S3_BUCKET: str | None = os.getenv("OILTRACE_S3_BUCKET") or None
    OILTRACE_S3_ENDPOINT_URL: str | None = os.getenv("OILTRACE_S3_ENDPOINT_URL") or None
    OILTRACE_S3_PREFIX: str = os.getenv("OILTRACE_S3_PREFIX", "oiltrace")

    WEIGHT_PROXIMITY: float = env_float("WEIGHT_PROXIMITY", 0.25)
    WEIGHT_TIMING: float = env_float("WEIGHT_TIMING", 0.20)
    WEIGHT_TRAJECTORY: float = env_float("WEIGHT_TRAJECTORY", 0.20)
    WEIGHT_DRIFT_OVERLAP: float = env_float("WEIGHT_DRIFT_OVERLAP", 0.15)
    WEIGHT_BEHAVIOR: float = env_float("WEIGHT_BEHAVIOR", 0.10)
    WEIGHT_VESSEL_TYPE: float = env_float("WEIGHT_VESSEL_TYPE", 0.10)
    AIS_SEARCH_RADIUS_KM: float = env_float("AIS_SEARCH_RADIUS_KM", 20.0)
    AIS_TIME_PAD_HOURS: float = env_float("AIS_TIME_PAD_HOURS", 6.0)

    for _dir in (MODELS_DIR, DATA_DIR, OCEAN_CACHE_DIR, UPLOADS_DIR, SENTINEL1_DIR, ARTIFACTS_DIR):
        _dir.mkdir(parents=True, exist_ok=True)


APP_MODES = ("development", "sih_demo", "staging", "production")


def synthetic_fallback_allowed() -> bool:
    """Single authority for synthetic/fallback substitution.

    FALLBACK allowed only in development. SCENARIO allowed only in sih_demo.
    staging/production must use real data or fail honestly.
    """
    return settings.APP_MODE == "development"


def scenario_allowed() -> bool:
    return settings.APP_MODE == "sih_demo"


def validate_app_mode() -> None:
    mode = settings.APP_MODE
    if mode not in APP_MODES:
        raise RuntimeError(f"Invalid APP_MODE={mode!r}. Must be one of {APP_MODES}.")
    cors = os.getenv("CORS_ORIGINS", "")
    if mode in ("staging", "production"):
        if settings.DATABASE_URL.startswith("sqlite"):
            raise RuntimeError(f"APP_MODE={mode} refuses SQLite. Set a Postgres/PostGIS DATABASE_URL.")
        if cors.strip() == "*" or "*" in [c.strip() for c in cors.split(",")]:
            raise RuntimeError(f"APP_MODE={mode} refuses CORS_ORIGINS='*'. Set explicit origins.")
        # Release audit: FALLBACK substitution is development-only; staging and
        # production must never reach synthetic drift/AIS paths (drift.py gates
        # on mode helpers only, this refuses the legacy flag at boot).
        if settings.ALLOW_LIVE_FALLBACK:
            raise RuntimeError(f"APP_MODE={mode} refuses ALLOW_LIVE_FALLBACK=true.")
        if not settings.OILTRACE_API_KEY:
            raise RuntimeError("APP_MODE=production requires OILTRACE_API_KEY.")
    if mode == "production":
        # Production promises real data: every provider must be credentialed,
        # otherwise the system could only serve FAILED states.
        missing = [n for n, v in (
            ("CMEMS_USERNAME", settings.CMEMS_USERNAME),
            ("CMEMS_PASSWORD", settings.CMEMS_PASSWORD),
            ("CDSAPI_KEY", settings.CDSAPI_KEY),
            ("GFW_API_TOKEN", settings.GFW_API_TOKEN),
        ) if not v]
        if missing:
            raise RuntimeError(f"APP_MODE=production requires provider credentials: {', '.join(missing)}.")
    # Scoring weights must always sum to 1.0 (single source of truth).
    try:
        from app.contracts import scoring_weights

        scoring_weights()
    except Exception as exc:
        raise RuntimeError(f"Invalid scoring weights: {exc}") from exc


settings = Settings()
