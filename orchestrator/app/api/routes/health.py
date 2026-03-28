from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.observability import metrics
from app.queue import get_redis

router = APIRouter()
logger = logging.getLogger("feature_factory.health")


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)):
    checks: dict[str, str] = {}
    reasons: dict[str, str] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("Readiness db check failed", exc_info=True)
        checks["db"] = "error"
        reasons["db"] = str(exc)

    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("Readiness redis check failed", exc_info=True)
        checks["redis"] = "error"
        reasons["redis"] = str(exc)

    ok = all(v == "ok" for v in checks.values())
    if not ok:
        reason = "; ".join(f"{name}: {message}" for name, message in reasons.items()) or "Unknown readiness failure"
        return JSONResponse(
            {
                "ok": False,
                "status": "unhealthy",
                "reason": reason,
                "checks": checks,
                "reasons": reasons,
            },
            status_code=503,
        )

    return {"ok": True, "status": "healthy", "checks": checks}


@router.get("/health/metrics")
def health_metrics():
    return {"ok": True, "metrics": metrics.snapshot()}


@router.get("/health/runtime")
def health_runtime():
    settings = get_settings()
    return {
        "ok": True,
        "runtime": settings.runtime_diagnostics(),
        "openrouter": {
            "configured": bool(str(getattr(settings, "openrouter_api_key", "") or "").strip()),
            "mini_model": str(
                getattr(settings, "openrouter_mini_model", "qwen/qwen3.5-9b") or "qwen/qwen3.5-9b"
            ).strip(),
            "frontier_model": str(
                getattr(settings, "openrouter_frontier_model", "anthropic/claude-opus-4-6")
                or "anthropic/claude-opus-4-6"
            ).strip(),
        },
    }
