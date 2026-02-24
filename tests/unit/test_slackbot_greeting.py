from __future__ import annotations

from app.slackbot import SLACK_INTRO_MESSAGE, _is_directed_to_bot


def test_is_directed_to_bot_true_for_direct_messages() -> None:
    event = {"channel_type": "im", "text": "hello"}
    assert _is_directed_to_bot(event, bot_user_id="U123") is True


def test_is_directed_to_bot_true_for_mentions() -> None:
    event = {"channel_type": "channel", "text": "hi <@U123> can you help?"}
    assert _is_directed_to_bot(event, bot_user_id="U123") is True


def test_is_directed_to_bot_false_without_bot_reference() -> None:
    event = {"channel_type": "channel", "text": "hello team"}
    assert _is_directed_to_bot(event, bot_user_id="U123") is False


def test_intro_message_describes_bot_capabilities() -> None:
    assert "Feature Factory Slack bot" in SLACK_INTRO_MESSAGE
    assert "collect requirements" in SLACK_INTRO_MESSAGE
    assert "kick off builds" in SLACK_INTRO_MESSAGE
