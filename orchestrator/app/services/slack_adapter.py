from __future__ import annotations

import json
from typing import Any, Optional

from rich.console import Console

from app.config import get_settings


console = Console()


class SlackAdapter:
    def post_thread_message(self, *, channel: str, thread_ts: str, text: str, blocks: list[dict] | None = None) -> None:
        raise NotImplementedError

    def post_channel_message(self, *, channel: str, text: str, blocks: list[dict] | None = None) -> None:
        raise NotImplementedError


class MockSlackAdapter(SlackAdapter):
    def post_thread_message(self, *, channel: str, thread_ts: str, text: str, blocks: list[dict] | None = None) -> None:
        console.print(f"[bold cyan][MOCK Slack][/bold cyan] #{channel} thread={thread_ts}: {text}")
        if blocks:
            console.print(json.dumps(blocks, indent=2))

    def post_channel_message(self, *, channel: str, text: str, blocks: list[dict] | None = None) -> None:
        console.print(f"[bold cyan][MOCK Slack][/bold cyan] #{channel}: {text}")
        if blocks:
            console.print(json.dumps(blocks, indent=2))


class RealSlackAdapter(SlackAdapter):
    def __init__(self, bot_token: str):
        from slack_sdk.web.client import WebClient

        self.client = WebClient(token=bot_token)
        self.settings = get_settings()

    def _allowed(self, channel: str, user: str | None = None) -> bool:
        allowed_channels = self.settings.slack_allowed_channel_set()
        allowed_users = self.settings.slack_allowed_user_set()

        if allowed_channels and channel not in allowed_channels:
            return False
        if user and allowed_users and user not in allowed_users:
            return False
        return True

    def post_thread_message(self, *, channel: str, thread_ts: str, text: str, blocks: list[dict] | None = None) -> None:
        if not self._allowed(channel):
            console.print(f"[yellow]Slack post blocked by allowlist (channel={channel}).[/yellow]")
            return
        self.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text, blocks=blocks)

    def post_channel_message(self, *, channel: str, text: str, blocks: list[dict] | None = None) -> None:
        if not self._allowed(channel):
            console.print(f"[yellow]Slack post blocked by allowlist (channel={channel}).[/yellow]")
            return
        self.client.chat_postMessage(channel=channel, text=text, blocks=blocks)


def get_slack_adapter() -> SlackAdapter:
    settings = get_settings()
    if settings.mock_mode or not settings.enable_slack_bot or not settings.slack_bot_token:
        return MockSlackAdapter()
    return RealSlackAdapter(settings.slack_bot_token)
