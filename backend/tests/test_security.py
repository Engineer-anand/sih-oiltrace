"""M16 security tests — SSRF guard, ID validation, rate limits, upload caps."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from fastapi import HTTPException  # noqa: E402

from app.config import settings  # noqa: E402
from app import security as sec  # noqa: E402


def test_ssrf_allows_listed_https_host():
    old = settings.SSRF_ALLOWED_HOSTS
    settings.SSRF_ALLOWED_HOSTS = ("earth-search.aws.element84.com",)
    try:
        url = "https://earth-search.aws.element84.com/v1/search"
        assert sec.validate_raster_url(url) == url
    finally:
        settings.SSRF_ALLOWED_HOSTS = old


def test_ssrf_rejects_scheme_host_and_private():
    old = settings.SSRF_ALLOWED_HOSTS
    settings.SSRF_ALLOWED_HOSTS = ("example.com",)
    try:
        for bad in (
            "http://example.com/x.tif",  # not https
            "https://evil.example/x.tif",  # not allowlisted
            "https://127.0.0.1/x.tif",
            "https://169.254.169.254/x.tif",
            "https://localhost/x.tif",
            "https:///missing-host",
        ):
            try:
                sec.validate_raster_url(bad)
                raise AssertionError(f"expected 422 for {bad}")
            except HTTPException as exc:
                assert exc.status_code == 422
    finally:
        settings.SSRF_ALLOWED_HOSTS = old


def test_incident_id_validation():
    assert sec.validate_incident_id("OS-ABCDEF12") == "OS-ABCDEF12"
    for bad in ("", "OS-xyz", "../OS-ABCDEF12", "OS-ABCDEF12/x", "job_abc", "OS-ABCDEF123"):
        try:
            sec.validate_incident_id(bad)
            raise AssertionError(f"expected 400 for {bad!r}")
        except HTTPException as exc:
            assert exc.status_code == 400


def test_rate_limiter_trips():
    from starlette.requests import Request

    old = settings.RATE_LIMIT_PER_MINUTE
    settings.RATE_LIMIT_PER_MINUTE = 2
    limiter = sec.RateLimiter()
    try:
        def _req():
            return Request({"type": "http", "method": "GET", "path": "/x",
                            "headers": [], "client": ("10.9.9.9", 1234)})

        limiter.check(_req())
        limiter.check(_req())
        try:
            limiter.check(_req())
            raise AssertionError("expected 429")
        except HTTPException as exc:
            assert exc.status_code == 429
    finally:
        settings.RATE_LIMIT_PER_MINUTE = old


def test_upload_cap_configured_and_example_clean():
    assert settings.MAX_UPLOAD_BYTES > 0
    example = os.path.join(os.path.dirname(__file__), "..", "..", ".env.example")
    with open(example, encoding="utf-8") as fh:
        text = fh.read()
    assert "CORS_ORIGINS=http://localhost:8000,http://localhost:5173" in text
    assert "ALLOW_LIVE_FALLBACK=false" in text
    for token in ("Sih_hanshbhai", "9072be22", "hf3f3kd", "asingh123456789"):
        assert token not in text
