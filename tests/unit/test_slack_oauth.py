from __future__ import annotations

from types import SimpleNamespace

import app.services.slack_oauth as slack_oauth_mod
from app.config import Settings


def test_resolve_slack_bot_token_rotates_and_saves_when_refresh_token_exists(monkeypatch) -> None:
    settings = Settings.model_construct(
        slack_client_id="client-id",
        slack_client_secret="client-secret",
        slack_bot_token="",
    )
    saved: list[object] = []

    class DummyStore:
        def save_bot(self, bot) -> None:
            saved.append(bot)

    runtime = SimpleNamespace(installation_store=DummyStore())
    original_bot = SimpleNamespace(bot_token="xoxb-old", bot_refresh_token="refresh-1", team_id="T123")
    rotated_bot = SimpleNamespace(bot_token="xoxb-new", bot_refresh_token="refresh-2", team_id="T123")

    class DummyRotator:
        def __init__(self, *, client_id: str, client_secret: str):
            assert client_id == "client-id"
            assert client_secret == "client-secret"

        def perform_bot_token_rotation(self, *, bot, minutes_before_expiration: int):
            assert bot is original_bot
            assert minutes_before_expiration == 120
            return rotated_bot

    monkeypatch.setattr(slack_oauth_mod, "TokenRotator", DummyRotator)
    monkeypatch.setattr(slack_oauth_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(slack_oauth_mod, "get_slack_oauth_runtime", lambda: runtime)
    monkeypatch.setattr(slack_oauth_mod, "find_installed_bot", lambda **_: original_bot)

    token = slack_oauth_mod.resolve_slack_bot_token(team_id="T123")
    assert token == "xoxb-new"
    assert saved == [rotated_bot]


def test_resolve_slack_bot_token_uses_existing_token_when_rotation_errors(monkeypatch) -> None:
    settings = Settings.model_construct(
        slack_client_id="client-id",
        slack_client_secret="client-secret",
        slack_bot_token="",
    )
    runtime = SimpleNamespace(installation_store=SimpleNamespace(save_bot=lambda _bot: None))
    original_bot = SimpleNamespace(bot_token="xoxb-old", bot_refresh_token="refresh-1", team_id="T123")

    class FailingRotator:
        def __init__(self, *, client_id: str, client_secret: str):
            _ = client_id
            _ = client_secret

        def perform_bot_token_rotation(self, *, bot, minutes_before_expiration: int):
            _ = bot
            _ = minutes_before_expiration
            raise RuntimeError("rotate failed")

    monkeypatch.setattr(slack_oauth_mod, "TokenRotator", FailingRotator)
    monkeypatch.setattr(slack_oauth_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(slack_oauth_mod, "get_slack_oauth_runtime", lambda: runtime)
    monkeypatch.setattr(slack_oauth_mod, "find_installed_bot", lambda **_: original_bot)

    token = slack_oauth_mod.resolve_slack_bot_token(team_id="T123")
    assert token == "xoxb-old"


def test_resolve_slack_bot_token_falls_back_to_static_token(monkeypatch) -> None:
    settings = Settings.model_construct(
        slack_bot_token="xoxb-static",
        slack_client_id="",
        slack_client_secret="",
    )
    monkeypatch.setattr(slack_oauth_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(slack_oauth_mod, "find_installed_bot", lambda **_: None)

    token = slack_oauth_mod.resolve_slack_bot_token(team_id="T123")
    assert token == "xoxb-static"
