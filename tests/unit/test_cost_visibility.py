"""Tests for cost visibility in Slack."""
from __future__ import annotations

from unittest.mock import MagicMock

import app.slackbot as slackbot_mod
from app.services.block_builders import build_app_home_blocks
from app.tasks.jobs import _build_cost_summary_text


def test_cost_summary_format():
    """Cost text should show dollars, calls, and tokens."""
    text = _build_cost_summary_text(
        [
            {
                "event_type": "llm_cost",
                "data": {
                    "cost_usd": 0.0123,
                    "tier": "frontier",
                    "tokens_in": 100,
                    "tokens_out": 200,
                },
            },
            {
                "event_type": "llm_cost",
                "data": {
                    "cost_usd": 0.0004,
                    "tier": "mini",
                    "tokens_in": 50,
                    "tokens_out": 10,
                },
            },
        ]
    )

    assert text is not None
    assert "$0.0127" in text
    assert "2 API calls" in text
    assert "360 tokens" in text


def test_status_subcommand_includes_cost_when_available(mock_settings, monkeypatch):
    """Status subcommand should include per-feature cost when available."""
    client = MagicMock()
    monkeypatch.setattr(
        slackbot_mod,
        "_fetch_user_recent_features",
        lambda settings, user_id: [
            {"id": "abc", "title": "Dark mode", "status": "BUILDING", "total_cost": 0.1234},
            {"id": "def", "title": "Export CSV", "status": "MERGED", "total_cost": 0.0},
        ],
    )

    slackbot_mod._handle_status_subcommand(
        {"user_id": "U123", "channel_id": "C123"},
        client,
        mock_settings,
    )

    text = client.chat_postEphemeral.call_args.kwargs["text"]
    assert "Dark mode" in text
    assert "$0.1234" in text
    assert "Export CSV" in text


def test_cost_subcommand_with_costs(mock_settings, monkeypatch):
    """Cost subcommand should show aggregate total and breakdown."""
    client = MagicMock()
    monkeypatch.setattr(
        slackbot_mod,
        "_fetch_all_user_features_with_costs",
        lambda settings, user_id: [
            {"title": "Dark mode", "total_cost": 0.1200},
            {"title": "Export CSV", "total_cost": 0.0305},
            {"title": "No spend yet", "total_cost": 0.0},
        ],
    )

    slackbot_mod._handle_cost_subcommand(
        {"user_id": "U123", "channel_id": "C123"},
        client,
        mock_settings,
    )

    text = client.chat_postEphemeral.call_args.kwargs["text"]
    assert "$0.1505" in text
    assert "Dark mode: $0.1200" in text
    assert "Export CSV: $0.0305" in text
    assert "No spend yet" not in text


def test_cost_subcommand_no_costs(mock_settings, monkeypatch):
    """Cost subcommand should handle zero-cost gracefully."""
    client = MagicMock()
    monkeypatch.setattr(
        slackbot_mod,
        "_fetch_all_user_features_with_costs",
        lambda settings, user_id: [],
    )

    slackbot_mod._handle_cost_subcommand(
        {"user_id": "U123", "channel_id": "C123"},
        client,
        mock_settings,
    )

    client.chat_postEphemeral.assert_called_once_with(
        channel="C123",
        user="U123",
        text="No OpenRouter costs recorded for your requests yet.",
    )


def test_app_home_includes_cost():
    """App Home should show total cost when provided."""
    blocks = build_app_home_blocks(
        app_name="Test",
        user_id="U1",
        recent_features=[],
        github_status="OK",
        total_cost=1.23,
    )
    assert "$1.2300" in str(blocks)


def test_app_home_hides_cost_when_zero():
    blocks = build_app_home_blocks(
        app_name="Test",
        user_id="U1",
        recent_features=[],
        github_status="OK",
        total_cost=0.0,
    )
    assert "spend" not in str(blocks).lower()
