from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import get_settings
from app.db import init_db


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Feature Factory",
        version="0.1.0",
        description="Local-first scaffold for Slack-driven feature building.",
    )

    @app.on_event("startup")
    def _startup() -> None:
        init_db()

    # Routes
    app.include_router(api_router)

    # Static
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app


app = create_app()
