from __future__ import annotations

from contextlib import contextmanager
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


def init_db(*, max_attempts: int = 20, retry_delay_seconds: float = 1.5) -> None:
    """Create tables if they do not exist.

    For production you should prefer Alembic migrations.
    For novice/local testing, this removes a big setup hurdle.
    """
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
