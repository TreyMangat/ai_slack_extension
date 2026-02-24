from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging

from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk.oauth.installation_store.models.bot import Bot
from slack_sdk.oauth.installation_store.sqlalchemy import SQLAlchemyInstallationStore
from slack_sdk.oauth.state_store.sqlalchemy import SQLAlchemyOAuthStateStore

from app.config import get_settings
from app.db import ENGINE


logger = logging.getLogger("feature_factory.slack_oauth")


@dataclass(frozen=True)
class SlackOAuthRuntime:
    installation_store: SQLAlchemyInstallationStore
    state_store: SQLAlchemyOAuthStateStore
    oauth_settings: OAuthSettings


@lru_cache
def get_slack_oauth_runtime() -> SlackOAuthRuntime | None:
    settings = get_settings()
    if not settings.slack_oauth_enabled():
        return None

    client_id = (settings.slack_client_id or "").strip()
    client_secret = (settings.slack_client_secret or "").strip()
    if not client_id or not client_secret:
        return None

    installation_store = SQLAlchemyInstallationStore(client_id=client_id, engine=ENGINE)
    state_store = SQLAlchemyOAuthStateStore(
        expiration_seconds=max(int(settings.slack_oauth_state_expiration_seconds), 60),
        engine=ENGINE,
    )

    oauth_settings = OAuthSettings(
        client_id=client_id,
        client_secret=client_secret,
        scopes=settings.slack_oauth_scopes_list(),
        user_scopes=settings.slack_oauth_user_scopes_list() or None,
        redirect_uri=settings.slack_oauth_redirect_uri_resolved() or None,
        install_path=settings.slack_oauth_install_path_normalized(),
        redirect_uri_path=settings.slack_oauth_callback_path_normalized(),
        installation_store=installation_store,
        state_store=state_store,
        installation_store_bot_only=True,
    )

    logger.info(
        "slack_oauth_enabled install_path=%s callback_path=%s",
        settings.slack_oauth_install_path_normalized(),
        settings.slack_oauth_callback_path_normalized(),
    )
    return SlackOAuthRuntime(
        installation_store=installation_store,
        state_store=state_store,
        oauth_settings=oauth_settings,
    )


def ensure_slack_oauth_schema() -> None:
    runtime = get_slack_oauth_runtime()
    if runtime is None:
        return
    runtime.installation_store.create_tables()
    runtime.state_store.create_tables()


def find_installed_bot(*, team_id: str = "", enterprise_id: str = "") -> Bot | None:
    runtime = get_slack_oauth_runtime()
    if runtime is None:
        return None
    normalized_team_id = (team_id or "").strip() or None
    normalized_enterprise_id = (enterprise_id or "").strip() or None
    return runtime.installation_store.find_bot(
        enterprise_id=normalized_enterprise_id,
        team_id=normalized_team_id,
        is_enterprise_install=False,
    )


def resolve_slack_bot_token(*, team_id: str = "", enterprise_id: str = "") -> str:
    bot = find_installed_bot(team_id=team_id, enterprise_id=enterprise_id)
    if bot and (bot.bot_token or "").strip():
        return bot.bot_token.strip()
    fallback = (get_settings().slack_bot_token or "").strip()
    if fallback:
        return fallback
    raise RuntimeError(
        "No Slack bot token available for this workspace. "
        "Install the app using Slack OAuth, or configure SLACK_BOT_TOKEN for single-workspace mode."
    )
