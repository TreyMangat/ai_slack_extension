from __future__ import annotations

import logging

import pytest

from app.config import Settings
import app.slackbot as slackbot_mod


def _settings(
    *,
    enable_slack_bot: bool = True,
    slack_mode: str = "socket",
    slack_bot_token: str = "xoxb-test",
    slack_app_token: str = "xapp-test",
) -> Settings:
    return Settings.model_construct(
        enable_slack_bot=enable_slack_bot,
        slack_mode=slack_mode,
        slack_bot_token=slack_bot_token,
        slack_app_token=slack_app_token,
        slack_signing_secret="signing-secret",
    )


def test_slackbot_exits_on_wrong_mode(monkeypatch, caplog) -> None:
    settings = _settings(slack_mode="http")

    monkeypatch.setattr(slackbot_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(slackbot_mod.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        slackbot_mod.time,
        "sleep",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sleep should not be called")),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(SystemExit) as excinfo:
            slackbot_mod.main()

    assert excinfo.value.code == 0
    assert "not 'socket'" in caplog.text


def test_slackbot_exits_on_missing_token(monkeypatch, caplog) -> None:
    settings = _settings(slack_bot_token="", slack_app_token="")

    monkeypatch.setattr(slackbot_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(slackbot_mod.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        slackbot_mod.time,
        "sleep",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("sleep should not be called")),
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as excinfo:
            slackbot_mod.main()

    assert excinfo.value.code == 1
    assert "SLACK_BOT_TOKEN or SLACK_APP_TOKEN is missing" in caplog.text


def test_slackbot_starts_on_correct_config(monkeypatch) -> None:
    settings = _settings()
    sentinel_app = object()
    observed: dict[str, object] = {}

    class DummySocketModeHandler:
        def __init__(self, app, token):
            observed["app"] = app
            observed["token"] = token

        def start(self):
            observed["started"] = True

    monkeypatch.setattr(slackbot_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(slackbot_mod.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(slackbot_mod, "create_slack_bolt_app", lambda cfg: sentinel_app)
    monkeypatch.setattr(slackbot_mod, "_socket_mode_handler_cls", lambda: DummySocketModeHandler)

    slackbot_mod.main()

    assert observed == {
        "app": sentinel_app,
        "token": "xapp-test",
        "started": True,
    }
