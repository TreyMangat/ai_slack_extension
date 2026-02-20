from __future__ import annotations

import redis
from rq import Queue

from app.config import get_settings


def get_redis():
    settings = get_settings()
    return redis.from_url(settings.redis_url)


def get_queue(name: str = "default") -> Queue:
    return Queue(name, connection=get_redis())
