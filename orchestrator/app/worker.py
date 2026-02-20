from __future__ import annotations

from rq import Connection, Worker

from app.queue import get_redis


def main() -> None:
    redis_conn = get_redis()
    with Connection(redis_conn):
        w = Worker(["default"])
        w.work(with_scheduler=False)


if __name__ == "__main__":
    main()
