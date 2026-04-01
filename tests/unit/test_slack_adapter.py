"""Tests for SlackAdapter interface and implementations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.slack_adapter import MockSlackAdapter, SlackAdapter


def test_adapter_has_all_required_methods():
    """Guard: adapter must implement the full interface."""
    required = {
        "post_thread_message",
        "post_channel_message",
        "update_message",
        "delete_message",
        "add_reaction",
    }
    methods = {m for m in dir(SlackAdapter) if not m.startswith("_")}
    assert required.issubset(methods)


def test_mock_adapter_post_thread_message_no_error():
    adapter = MockSlackAdapter()
    adapter.post_thread_message(channel="C1", thread_ts="123", text="hello")


def test_mock_adapter_post_channel_message_no_error():
    adapter = MockSlackAdapter()
    adapter.post_channel_message(channel="C1", text="hello")


def test_mock_adapter_update_message_no_error():
    adapter = MockSlackAdapter()
    adapter.update_message(channel="C1", ts="123", text="updated")


def test_mock_adapter_update_message_with_blocks():
    adapter = MockSlackAdapter()
    adapter.update_message(
        channel="C1",
        ts="123",
        text="updated",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}],
    )


def test_mock_adapter_delete_message_no_error():
    adapter = MockSlackAdapter()
    adapter.delete_message(channel="C1", ts="123")


def test_mock_adapter_add_reaction_no_error():
    adapter = MockSlackAdapter()
    adapter.add_reaction(channel="C1", timestamp="123", name="thumbsup")


def test_base_adapter_raises_not_implemented():
    adapter = SlackAdapter()
    import pytest

    with pytest.raises(NotImplementedError):
        adapter.post_thread_message(channel="C1", thread_ts="t", text="t")
    with pytest.raises(NotImplementedError):
        adapter.post_channel_message(channel="C1", text="t")
    with pytest.raises(NotImplementedError):
        adapter.update_message(channel="C1", ts="t", text="t")
    with pytest.raises(NotImplementedError):
        adapter.delete_message(channel="C1", ts="t")
    with pytest.raises(NotImplementedError):
        adapter.add_reaction(channel="C1", timestamp="t", name="t")
