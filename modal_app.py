from __future__ import annotations

import os
from pathlib import Path

import modal


APP_NAME = "feature-factory"
DEFAULT_ENV_SECRET_NAME = "feature-factory-env"


def _int_env(name: str, default: int, *, minimum: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> list[str]:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


_api_min_containers = _int_env("MODAL_API_MIN_CONTAINERS", default=0, minimum=0)
_api_max_containers = _int_env("MODAL_API_MAX_CONTAINERS", default=1, minimum=1)
if _api_max_containers < _api_min_containers:
    _api_max_containers = _api_min_containers

_api_concurrency = _int_env("MODAL_API_ALLOW_CONCURRENT_INPUTS", default=8, minimum=1)
_queue_drain_seconds = _int_env("MODAL_QUEUE_DRAIN_SECONDS", default=180, minimum=10)
_cleanup_interval_minutes = _int_env("MODAL_CLEANUP_INTERVAL_MINUTES", default=120, minimum=5)
_skip_worker_when_queue_empty = _bool_env("MODAL_SKIP_WORKER_WHEN_QUEUE_EMPTY", default=True)
_primary_secret_name = (os.getenv("MODAL_ENV_SECRET_NAME", "") or "").strip() or DEFAULT_ENV_SECRET_NAME

_secret_names = [_primary_secret_name]
for name in _csv_env("MODAL_EXTRA_SECRET_NAMES"):
    if name not in _secret_names:
        _secret_names.append(name)

# Keep this image source aligned with the existing orchestrator runtime.
image = modal.Image.from_dockerfile("orchestrator/Dockerfile", context_dir="orchestrator")
if _bool_env("MODAL_INCLUDE_OPENCLAW_AUTH", default=False):
    local_auth_dir = Path((os.getenv("MODAL_OPENCLAW_AUTH_LOCAL_DIR", "") or "").strip() or "secrets/openclaw")
    # This block runs in both local deploy context and remote runtime import.
    # Only local deploy has access to the host path used by add_local_dir.
    if local_auth_dir.exists() and local_auth_dir.is_dir():
        # Bundle OpenClaw auth seed into the image for cloud execution.
        image = image.add_local_dir(
            local_path=str(local_auth_dir),
            remote_path="/run/secrets/openclaw",
            copy=True,
        )

app = modal.App(APP_NAME)

_common_env = {
    "APP_ENV": (os.getenv("APP_ENV", "") or "").strip() or "prod",
}
_shared_kwargs = {
    "image": image,
    "secrets": [modal.Secret.from_name(name) for name in _secret_names],
}


@app.function(
    **_shared_kwargs,
    env=_common_env,
    min_containers=_api_min_containers,
    max_containers=_api_max_containers,
    timeout=60 * 60 * 24,
    allow_concurrent_inputs=_api_concurrency,
)
@modal.asgi_app()
def api():
    from app.main import app as fastapi_app

    return fastapi_app


@app.function(
    **_shared_kwargs,
    env={**_common_env, "WORKER_BURST_MODE": "true"},
    schedule=modal.Period(seconds=_queue_drain_seconds),
    timeout=60 * 10,
)
def drain_queue_once() -> None:
    if _skip_worker_when_queue_empty:
        try:
            from app.queue import get_redis

            if int(get_redis().llen("rq:queue:default")) <= 0:
                return
        except Exception:
            # If the preflight check fails, fall back to normal worker startup.
            pass

    from app.worker import main as worker_main

    worker_main()


@app.function(
    **_shared_kwargs,
    env={**_common_env, "CLEANUP_RUN_ONCE": "true"},
    schedule=modal.Period(minutes=_cleanup_interval_minutes),
    timeout=60 * 10,
)
def cleanup_once() -> None:
    from app.cleanup_worker import main as cleanup_main

    cleanup_main()
