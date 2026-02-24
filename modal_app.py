from __future__ import annotations

import modal


APP_NAME = "feature-factory"
ENV_SECRET_NAME = "feature-factory-env"

# Keep this image source aligned with the existing orchestrator runtime.
image = modal.Image.from_dockerfile("orchestrator/Dockerfile")
app = modal.App(APP_NAME)

_common_env = {
    "APP_ENV": "prod",
    "RUN_MIGRATIONS": "true",
}
_shared_kwargs = {
    "image": image,
    "secrets": [modal.Secret.from_name(ENV_SECRET_NAME)],
}


@app.function(
    **_shared_kwargs,
    env=_common_env,
    min_containers=1,
    max_containers=3,
    timeout=60 * 60 * 24,
    allow_concurrent_inputs=200,
)
@modal.asgi_app()
def api():
    from app.main import app as fastapi_app

    return fastapi_app


@app.function(
    **_shared_kwargs,
    env={**_common_env, "WORKER_BURST_MODE": "true"},
    schedule=modal.Period(seconds=20),
    timeout=60 * 10,
)
def drain_queue_once() -> None:
    from app.worker import main as worker_main

    worker_main()


@app.function(
    **_shared_kwargs,
    env={**_common_env, "CLEANUP_RUN_ONCE": "true"},
    schedule=modal.Period(minutes=5),
    timeout=60 * 10,
)
def cleanup_once() -> None:
    from app.cleanup_worker import main as cleanup_main

    cleanup_main()
