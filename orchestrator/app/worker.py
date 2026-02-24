from __future__ import annotations

import json
import logging
import os

from rq import Connection, Worker

from app.config import get_settings
from app.queue import get_redis
from app.services.openclaw_runtime import stage_openclaw_auth_if_needed


def main() -> None:
    logger = logging.getLogger("feature_factory.worker")
    settings = get_settings()
    staged = stage_openclaw_auth_if_needed(settings)
    logger.info("openclaw_auth_stage %s", json.dumps(staged, sort_keys=True))
    settings.validate_runtime_guardrails()
    settings.validate_startup_prerequisites()
    redis_conn = get_redis()
    burst = os.getenv("WORKER_BURST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    with Connection(redis_conn):
        w = Worker(["default"])
        w.work(with_scheduler=False, burst=burst)


if __name__ == "__main__":
    main()
