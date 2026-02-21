from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import get_settings
from app.db import init_db
from app.observability import configure_json_logging, install_request_observability


def create_app() -> FastAPI:
    settings = get_settings()
    configure_json_logging()

    app = FastAPI(
        title="Feature Factory",
        version="0.1.0",
        description="Local-first scaffold for Slack-driven feature building.",
        docs_url="/docs" if settings.docs_enabled() else None,
        redoc_url="/redoc" if settings.docs_enabled() else None,
        openapi_url="/openapi.json" if settings.docs_enabled() else None,
    )

    @app.on_event("startup")
    def _startup() -> None:
        settings.validate_runtime_guardrails()
        init_db()

    # Routes
    app.include_router(api_router)
    install_request_observability(app)

    # Static
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app


app = create_app()
