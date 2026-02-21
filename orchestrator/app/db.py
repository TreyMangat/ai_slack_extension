from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import time

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base


def _make_engine():
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


ENGINE = _make_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ENGINE)


def _should_run_migrations() -> bool:
    settings = get_settings()
    env = (settings.app_env or "").strip().lower()
    return settings.run_migrations or env in {"prod", "production"}


def _run_alembic_upgrade(*, max_attempts: int, retry_delay_seconds: float) -> None:
    settings = get_settings()
    alembic_ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    upgrade_cmd = ["alembic", "-c", str(alembic_ini), "upgrade", "head"]
    stamp_cmd = ["alembic", "-c", str(alembic_ini), "stamp", "head"]
    env = os.environ.copy()
    env["DATABASE_URL"] = settings.database_url

    last_error: str = ""
    for attempt in range(1, max_attempts + 1):
        proc = subprocess.run(upgrade_cmd, check=False, capture_output=True, text=True, env=env)
        if proc.returncode == 0:
            return

        last_error = (proc.stderr or proc.stdout or "").strip()
        duplicate_table = "already exists" in last_error.lower()
        if settings.migration_bootstrap_stamp and duplicate_table:
            stamp_proc = subprocess.run(stamp_cmd, check=False, capture_output=True, text=True, env=env)
            if stamp_proc.returncode == 0:
                return
            last_error = (stamp_proc.stderr or stamp_proc.stdout or "").strip() or last_error

        if attempt == max_attempts:
            break
        time.sleep(retry_delay_seconds)

    raise RuntimeError(last_error or "alembic upgrade failed")


def _create_all_with_retry(*, max_attempts: int, retry_delay_seconds: float) -> None:
    last_error: OperationalError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            Base.metadata.create_all(bind=ENGINE)
            return
        except OperationalError as err:
            last_error = err
            if attempt == max_attempts:
                break
            time.sleep(retry_delay_seconds)

    if last_error:
        raise last_error


def init_db(*, max_attempts: int = 20, retry_delay_seconds: float = 1.5) -> None:
    """Initialize schema state.

    - Production path (`APP_ENV=prod` or `RUN_MIGRATIONS=true`): run Alembic migrations.
    - Local/dev path: create tables automatically for novice-friendly setup.
    """
    if _should_run_migrations():
        _run_alembic_upgrade(max_attempts=max_attempts, retry_delay_seconds=retry_delay_seconds)
        return
    _create_all_with_retry(max_attempts=max_attempts, retry_delay_seconds=retry_delay_seconds)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
