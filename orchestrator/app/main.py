from __future__ import annotations

import json
import logging
from html import escape
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import get_settings
from app.db import init_db
from app.observability import configure_json_logging, install_request_observability
from app.services.github_user_oauth import (
    build_github_oauth_authorize_url,
    complete_github_oauth_callback,
)
from app.services.openclaw_runtime import stage_openclaw_auth_if_needed
from app.services.slack_oauth import ensure_slack_oauth_schema


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
        settings.validate_runtime_guardrails()
        settings.validate_startup_prerequisites()
        logger.info("runtime_diagnostics %s", json.dumps(settings.runtime_diagnostics(), sort_keys=True))
        init_db()
        ensure_slack_oauth_schema()

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

            if settings.slack_oauth_enabled():
                install_path = settings.slack_oauth_install_path_normalized()
                callback_path = settings.slack_oauth_callback_path_normalized()

                @app.get(install_path)
                async def slack_oauth_install(request: Request):
                    return await slack_handler.handle(request)

                @app.get(callback_path)
                async def slack_oauth_callback(request: Request):
                    return await slack_handler.handle(request)

    install_request_observability(app)

    # Static
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    if settings.github_user_oauth_enabled():
        github_install_path = settings.github_oauth_install_path_normalized()
        github_callback_path = settings.github_oauth_callback_path_normalized()

        @app.get(github_install_path)
        async def github_user_oauth_install(
            slack_user_id: str = "",
            slack_team_id: str = "",
            next: str = "",  # noqa: A002
        ):
            try:
                authorize_url = build_github_oauth_authorize_url(
                    slack_user_id=slack_user_id,
                    slack_team_id=slack_team_id,
                    next_url=next,
                )
            except Exception as e:  # noqa: BLE001
                return JSONResponse(status_code=400, content={"detail": str(e)})
            return RedirectResponse(url=authorize_url, status_code=302)

        @app.get(github_callback_path)
        async def github_user_oauth_callback(code: str = "", state: str = ""):
            try:
                result = complete_github_oauth_callback(code=code, state=state)
            except Exception as e:  # noqa: BLE001
                return HTMLResponse(
                    status_code=400,
                    content=(
                        "<h2>GitHub connection failed</h2>"
                        f"<p>{escape(str(e))}</p>"
                        "<p>Return to Slack and run <code>/prfactory-github</code> to try again.</p>"
                    ),
                )

            next_url = (result.next_url or "").strip()
            if next_url:
                parsed = urlsplit(next_url)
                # Only allow relative paths to avoid open redirects.
                if not parsed.scheme and not parsed.netloc and next_url.startswith("/"):
                    return RedirectResponse(url=next_url, status_code=302)

            return HTMLResponse(
                status_code=200,
                content=(
                    "<h2>GitHub connected successfully</h2>"
                    f"<p>Connected Slack user <code>{escape(result.slack_user_id)}</code> "
                    f"to GitHub user <code>{escape(result.github_login)}</code>.</p>"
                    "<p>You can close this tab and return to Slack.</p>"
                ),
            )

    return app


app = create_app()
