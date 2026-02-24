from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import get_settings
from app.db import init_db
from app.observability import configure_json_logging, install_request_observability
from app.services.openclaw_runtime import stage_openclaw_auth_if_needed


def create_app() -> FastAPI:
    settings = get_settings()
    configure_json_logging()
    logger = logging.getLogger("feature_factory.startup")

    app = FastAPI(
        title=settings.app_display_name or "PRFactory",
        version="0.1.0",
        description="Slack-driven PR automation orchestrator.",
        docs_url="/docs" if settings.docs_enabled() else None,
        redoc_url="/redoc" if settings.docs_enabled() else None,
        openapi_url="/openapi.json" if settings.docs_enabled() else None,
    )

    @app.on_event("startup")
    def _startup() -> None:
        staged = stage_openclaw_auth_if_needed(settings)
        logger.info("openclaw_auth_stage %s", json.dumps(staged, sort_keys=True))
        if settings.enable_slack_bot and settings.slack_mode_normalized() == "http":
            if not (settings.slack_bot_token or "").strip():
                raise RuntimeError("ENABLE_SLACK_BOT=true and SLACK_MODE=http require SLACK_BOT_TOKEN")
            if not (settings.slack_signing_secret or "").strip():
                raise RuntimeError("ENABLE_SLACK_BOT=true and SLACK_MODE=http require SLACK_SIGNING_SECRET")
        settings.validate_runtime_guardrails()
        settings.validate_startup_prerequisites()
        logger.info("runtime_diagnostics %s", json.dumps(settings.runtime_diagnostics(), sort_keys=True))
        init_db()

    # Routes
    app.include_router(api_router)
    if settings.enable_slack_bot and settings.slack_mode_normalized() == "http":
        try:
            from slack_bolt.adapter.fastapi import SlackRequestHandler

            from app.slackbot import create_slack_bolt_app

            slack_handler = SlackRequestHandler(create_slack_bolt_app(settings))
        except Exception as e:  # noqa: BLE001
            logger.exception("slack_http_init_failed error=%s", e)
            slack_handler = None

        if slack_handler is not None:

            @app.post("/api/slack/events")
            async def slack_events(request: Request):
                return await slack_handler.handle(request)

    install_request_observability(app)

    # Static
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    return app


app = create_app()
