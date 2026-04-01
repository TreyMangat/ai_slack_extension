from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_settings
from app.services.slack_oauth import resolve_slack_bot_token

logger = logging.getLogger(__name__)


class SlackAdapter:
    def post_thread_message(
        self,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: list[dict] | None = None,
        team_id: str = "",
    ) -> None:
        raise NotImplementedError

    def post_channel_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
        team_id: str = "",
    ) -> None:
        raise NotImplementedError


class MockSlackAdapter(SlackAdapter):
    def post_thread_message(
        self,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: list[dict] | None = None,
        team_id: str = "",
    ) -> None:
        logger.info("mock_slack_thread_post channel=%s thread_ts=%s text=%s", channel, thread_ts, text)
        if blocks:
            logger.debug("mock_slack_thread_blocks channel=%s thread_ts=%s blocks=%s", channel, thread_ts, json.dumps(blocks, indent=2))

    def post_channel_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
        team_id: str = "",
    ) -> None:
        logger.info("mock_slack_channel_post channel=%s text=%s", channel, text)
        if blocks:
            logger.debug("mock_slack_channel_blocks channel=%s blocks=%s", channel, json.dumps(blocks, indent=2))


class RealSlackAdapter(SlackAdapter):
    def __init__(self, bot_token: str = ""):
        from slack_sdk.web.client import WebClient

        self._default_token = (bot_token or "").strip()
        self._clients: dict[str, WebClient] = {}
        if self._default_token:
            self._clients[self._default_token] = WebClient(token=self._default_token)
        self.settings = get_settings()

    def _resolve_client(self, *, team_id: str = ""):
        token = resolve_slack_bot_token(team_id=team_id) if self.settings.slack_oauth_enabled() else self._default_token
        if not token:
            token = resolve_slack_bot_token(team_id=team_id)
        client = self._clients.get(token)
        if client is not None:
            return client
        from slack_sdk.web.client import WebClient

        client = WebClient(token=token)
        self._clients[token] = client
        return client

    def _allowed(self, channel: str, user: str | None = None) -> bool:
        allowed_channels = self.settings.slack_allowed_channel_set()
        allowed_users = self.settings.slack_allowed_user_set()

        if allowed_channels and channel not in allowed_channels:
            return False
        if user and allowed_users and user not in allowed_users:
            return False
        return True

    def post_thread_message(
        self,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        blocks: list[dict] | None = None,
        team_id: str = "",
    ) -> None:
        if not self._allowed(channel):
            logger.warning("slack_thread_post_blocked_by_allowlist channel=%s", channel)
            return
        try:
            client = self._resolve_client(team_id=team_id)
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text, blocks=blocks)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "slack_thread_post_failed team=%s channel=%s error=%s",
                team_id,
                channel,
                e,
                exc_info=True,
            )

    def post_channel_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
        team_id: str = "",
    ) -> None:
        if not self._allowed(channel):
            logger.warning("slack_channel_post_blocked_by_allowlist channel=%s", channel)
            return
        try:
            client = self._resolve_client(team_id=team_id)
            client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "slack_channel_post_failed team=%s channel=%s error=%s",
                team_id,
                channel,
                e,
                exc_info=True,
            )


def get_slack_adapter() -> SlackAdapter:
    settings = get_settings()
    if not settings.enable_slack_bot:
        return MockSlackAdapter()
    if not settings.slack_bot_token and not settings.slack_oauth_enabled():
        return MockSlackAdapter()
    return RealSlackAdapter(settings.slack_bot_token)
