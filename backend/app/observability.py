"""Observability primitives (M15): correlation IDs, metrics, readiness.

Dependency-free (no prometheus client): in-memory counters/histograms with a
JSON snapshot endpoint. Correlation flows via contextvars so job/stage code
can tag logs without threading request objects through every call.
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("oiltrace_request_id", default="-")
job_id_ctx: ContextVar[str] = ContextVar("oiltrace_job_id", default="-")
investigation_ctx: ContextVar[str] = ContextVar("oiltrace_investigation_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class ContextFilter(logging.Filter):
    """Appends [req=.. job=.. inv=..] to records that lack them (M15)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            suffix = f" [req={request_id_ctx.get()} job={job_id_ctx.get()} inv={investigation_ctx.get()}]"
            if not str(record.getMessage()).endswith("]") or "req=" not in str(record.getMessage()):
                record.msg = f"{record.msg}{suffix}"
        except Exception:
            pass
        return True


_configured = False
_config_lock = threading.Lock()


def setup() -> None:
    global _configured
    with _config_lock:
        if _configured:
            return
        filtr = ContextFilter()
        root = logging.getLogger()
        for handler in root.handlers:
            handler.addFilter(filtr)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).addFilter(filtr)
        _configured = True


class Metrics:
    """Thread-safe in-memory counters + latency samples (M15)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._samples: dict[str, list[float]] = {}

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def observe(self, name: str, value_ms: float, keep: int = 500) -> None:
        with self._lock:
            series = self._samples.setdefault(name, [])
            series.append(float(value_ms))
            if len(series) > keep:
                del series[: len(series) - keep]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {"counters": dict(self._counters), "latency_ms": {}}
            for name, series in self._samples.items():
                if not series:
                    continue
                ordered = sorted(series)
                out["latency_ms"][name] = {
                    "count": len(ordered),
                    "p50": round(statistics.median(ordered), 2),
                    "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
                    "max": round(ordered[-1], 2),
                }
            return out

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._samples.clear()


metrics = Metrics()


def check_readiness(db_ok: bool, model_ok: bool, storage_ok: bool,
                    providers: dict[str, dict] | None = None) -> dict:
    """Pure readiness computation (M15): liveness vs readiness split.

    providers: {name: {"configured": bool, "fresh": bool|None}} — presence of
    credentials only, never values.
    """
    providers = providers or {}
    deps: dict[str, str] = {
        "database": "ok" if db_ok else "fail",
        "model": "ok" if model_ok else "fail",
        "storage": "ok" if storage_ok else "fail",
    }
    for name, info in providers.items():
        if not info.get("configured"):
            deps[f"provider:{name}"] = "unconfigured"
        elif info.get("fresh") is False:
            deps[f"provider:{name}"] = "stale"
        else:
            deps[f"provider:{name}"] = "ok"
    status = "ready" if all(v == "ok" or v == "unconfigured" for v in deps.values()) else "not-ready"
    if deps.get("database") == "fail":
        status = "not-ready"
    return {"status": status, "dependencies": deps, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
