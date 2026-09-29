"""Security helpers: SSRF guard, path validation, auth, rate limiting."""
from __future__ import annotations

import ipaddress
import re
import time
from collections import defaultdict
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from app.config import settings

INCIDENT_ID_RE = re.compile(r"^OS-[A-F0-9]{8}$")
JOB_ID_RE = re.compile(r"^job_[a-f0-9]{12}$")

BLOCKED_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def validate_incident_id(incident_id: str) -> str:
    if not INCIDENT_ID_RE.match(incident_id or ""):
        raise HTTPException(status_code=400, detail={"type": "invalid-id", "title": "Invalid incident ID", "detail": "incident_id must match OS-XXXXXXXX."})
    return incident_id


def validate_raster_url(url: str) -> str:
    """SSRF guard for remote raster access. Raises 422 on violation."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=422, detail={"type": "invalid-url", "title": "Invalid raster URL", "detail": "Unparseable URL."})
    if parsed.scheme != "https":
        raise HTTPException(status_code=422, detail={"type": "invalid-url", "title": "Invalid raster URL", "detail": "Only https:// raster URLs are allowed."})
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=422, detail={"type": "invalid-url", "title": "Invalid raster URL", "detail": "Missing host."})
    allowed = settings.SSRF_ALLOWED_HOSTS
    if allowed and not any(host == h or host.endswith("." + h) for h in allowed):
        raise HTTPException(status_code=422, detail={"type": "forbidden-host", "title": "Raster host not allowlisted", "detail": f"Host '{host}' is not allowlisted."})
    try:
        ip = ipaddress.ip_address(host)
        if any(ip in net for net in BLOCKED_NETS):
            raise HTTPException(status_code=422, detail={"type": "blocked-address", "title": "Blocked address", "detail": "Private/link-local addresses are blocked."})
    except ValueError:
        pass  # hostname, not literal IP
    if host in ("localhost",) or host.startswith("metadata."):
        raise HTTPException(status_code=422, detail={"type": "blocked-address", "title": "Blocked address", "detail": "Host is blocked."})
    return url


async def require_api_key(request: Request, key: str | None = Depends(api_key_header)):
    """Optional auth: enforced only when OILTRACE_API_KEY is configured."""
    expected = settings.OILTRACE_API_KEY
    if not expected:
        return None
    # Public read-only meta endpoints stay open.
    if request.url.path in ("/health", "/", "/api/v1/db/status", "/docs", "/openapi.json"):
        return None
    if key != expected:
        raise HTTPException(status_code=401, detail={"type": "unauthorized", "title": "Unauthorized", "detail": "Valid X-API-Key required."})
    return key


class RateLimiter:
    """Minimal in-process token bucket per client IP. Single-process SIH scope."""

    def __init__(self, per_minute: int = 60):
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, request: Request) -> None:
        path = request.url.path
        if path.startswith(("/app", "/sat", "/assets", "/docs", "/openapi.json", "/health", "/api/v1/pipeline/jobs")):
            return
        limit = settings.RATE_LIMIT_PER_MINUTE or self.per_minute
        client = request.client.host if request.client else "unknown"
        now = time.time()
        window = [t for t in self._hits[client] if now - t < 60]
        if len(window) >= limit:
            raise HTTPException(status_code=429, detail={"type": "rate-limited", "title": "Rate limit exceeded", "detail": "Slow down and retry."})
        window.append(now)
        if window:
            self._hits[client] = window
        else:
            self._hits.pop(client, None)


rate_limiter = RateLimiter()
