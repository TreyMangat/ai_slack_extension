"""Tests for App Home view and status subcommand."""
from __future__ import annotations

from unittest.mock import MagicMock

import app.slackbot as slackbot_mod
from app.services.block_builders import _status_emoji, build_app_home_blocks


def test_app_home_blocks_with_features():
    blocks = build_app_home_blocks(
        app_name="PRFactory",
        user_id="U123",
        recent_features=[
            {"id": "abc", "title": "Dark mode", "status": "BUILDING"},
            {"id": "def", "title": "Export CSV", "status": "MERGED"},
        ],
        github_status="Connected as @trey",
        new_request_url="https://slack.com/app_redirect?app=A123",
    )
    text = str(blocks)
    assert "Dark mode" in text
    assert "BUILDING" in text
    assert "Export CSV" in text
    assert "New request" in text
    assert "Connected as @trey" in text


def test_app_home_blocks_empty():
    blocks = build_app_home_blocks(
        app_name="PRFactory",
        user_id="U123",
        recent_features=[],
        github_status="Not connected",
    )
    text = str(blocks)
    assert "No feature requests yet" in text


def test_status_emoji_known():
    assert _status_emoji("BUILDING") != ":grey_question:"
    assert _status_emoji("MERGED") == ":tada:"


def test_status_emoji_unknown():
    assert _status_emoji("NONSENSE") == ":grey_question:"


def test_app_home_blocks_truncates_to_5():
    features = [
        {"id": f"f{i}", "title": f"Feature {i}", "status": "NEW"}
        for i in range(10)
    ]
    blocks = build_app_home_blocks(
        app_name="Test",
        user_id="U1",
        recent_features=features,
        github_status="OK",
    )
    text = str(blocks)
    assert "Feature 4" in text
    assert "Feature 5" not in text
    assert "f9" not in text


def test_fetch_user_recent_features_reads_items_payload(mock_settings, monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "items": [
                    {"id": f"f{i}", "title": f"Feature {i}", "status": "NEW"}
                    for i in range(6)
                ]
            }

    def fake_get(url, *, params, timeout, headers):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        captured["headers"] = headers
        return DummyResponse()

    monkeypatch.setattr(slackbot_mod.httpx, "get", fake_get)

    recent = slackbot_mod._fetch_user_recent_features(mock_settings, "U123")

    assert len(recent) == 5
    assert captured["url"] == "http://api:8000/api/feature-requests"
    assert captured["params"] == {"limit": 5, "mine": True}
    assert captured["timeout"] == 10
    assert captured["headers"]["X-FF-Token"] == "test-token"
    assert "U123" in captured["headers"].values()


def test_handle_status_subcommand_posts_recent_requests(mock_settings, monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(
        slackbot_mod,
        "_fetch_user_recent_features",
        lambda settings, user_id: [
            {"id": "abc", "title": "Dark mode", "status": "BUILDING"},
            {"id": "def", "title": "Export CSV", "status": "MERGED"},
        ],
    )

    slackbot_mod._handle_status_subcommand(
        {"user_id": "U123", "channel_id": "C123"},
        client,
        mock_settings,
    )

    client.chat_postEphemeral.assert_called_once()
    kwargs = client.chat_postEphemeral.call_args.kwargs
    assert kwargs["channel"] == "C123"
    assert kwargs["user"] == "U123"
    assert "*Your recent feature requests:*" in kwargs["text"]
    assert "Dark mode" in kwargs["text"]
    assert "MERGED" in kwargs["text"]


def test_handle_status_subcommand_posts_empty_state(mock_settings, monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(slackbot_mod, "_fetch_user_recent_features", lambda settings, user_id: [])

    slackbot_mod._handle_status_subcommand(
        {"user_id": "U123", "channel_id": "C123"},
        client,
        mock_settings,
    )

    client.chat_postEphemeral.assert_called_once_with(
        channel="C123",
        user="U123",
        text="You don't have any feature requests yet.",
    )
