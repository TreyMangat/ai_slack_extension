from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.observability import metrics
from app.queue import get_redis

router = APIRouter()


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)):
    checks: dict[str, str] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"

    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    ok = all(v == "ok" for v in checks.values())
    if not ok:
        raise HTTPException(status_code=503, detail={"ok": False, "checks": checks})

    return {"ok": True, "checks": checks}


@router.get("/health/metrics")
def health_metrics():
    return {"ok": True, "metrics": metrics.snapshot()}
