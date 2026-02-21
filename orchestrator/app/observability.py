from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request


request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for production-friendly logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key in ("method", "path", "status_code", "duration_ms", "feature_id", "job_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=True)


def configure_json_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, int] = {
            "http_requests_total": 0,
            "http_request_failures_total": 0,
            "build_jobs_started_total": 0,
            "build_jobs_failed_total": 0,
            "build_jobs_succeeded_total": 0,
        }
        self._timers_ms: dict[str, list[float]] = {"http_request_duration_ms": []}

    def inc(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def observe_ms(self, key: str, value_ms: float) -> None:
        with self._lock:
            bucket = self._timers_ms.setdefault(key, [])
            bucket.append(value_ms)
            if len(bucket) > 1000:
                del bucket[: len(bucket) - 1000]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            timers = {
                key: {
                    "count": len(values),
                    "avg_ms": (sum(values) / len(values)) if values else 0.0,
                    "p95_ms": _percentile(values, 95),
                }
                for key, values in self._timers_ms.items()
            }
            return {"counters": dict(self._counters), "timers": timers}


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, int((pct / 100.0) * (len(sorted_values) - 1))))
    return sorted_values[index]


metrics = MetricsRegistry()


def install_request_observability(app: FastAPI) -> None:
    logger = logging.getLogger("feature_factory.http")

    @app.middleware("http")
    async def _request_middleware(request: Request, call_next):
        rid = (request.headers.get("X-Request-ID") or "").strip() or str(uuid.uuid4())
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        metrics.inc("http_requests_total", 1)
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000.0
            metrics.observe_ms("http_request_duration_ms", duration_ms)
            response.headers["X-Request-ID"] = rid
            logger.info(
                "request_complete",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            if response.status_code >= 500:
                metrics.inc("http_request_failures_total", 1)
            return response
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000.0
            metrics.observe_ms("http_request_duration_ms", duration_ms)
            metrics.inc("http_request_failures_total", 1)
            logger.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        finally:
            request_id_ctx.reset(token)

