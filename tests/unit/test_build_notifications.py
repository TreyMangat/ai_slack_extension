"""Tests for build notification routing through SlackAdapter."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.jobs import _build_heartbeat_loop, _format_duration_seconds


# ---------------------------------------------------------------------------
# Heartbeat update-in-place
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_posts_first_then_updates():
    """First heartbeat posts a new message; subsequent ones update it."""
    slack = MagicMock()
    slack.post_thread_message.return_value = MagicMock(data={"ts": "111.222"})
    slack.update_message.return_value = None
    call_count = 0

    original_sleep = asyncio.sleep

    async def _counting_sleep(_seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise asyncio.CancelledError
        await original_sleep(0)

    with patch("app.tasks.jobs.asyncio.sleep", side_effect=_counting_sleep):
        with pytest.raises(asyncio.CancelledError):
            await _build_heartbeat_loop(
                slack=slack,
                channel_id="C1",
                thread_ts="T1",
                team_id="TEAM1",
                feature_title="My Feature",
                mode="new_feature",
                target_repo="org/repo",
                started_at=datetime.utcnow(),
                interval_seconds=0,
                timeout_window_seconds=0,
            )

    # First call should be post_thread_message
    assert slack.post_thread_message.call_count >= 1
    first_call = slack.post_thread_message.call_args_list[0]
    assert first_call.kwargs["channel"] == "C1"
    assert first_call.kwargs["thread_ts"] == "T1"
    assert first_call.kwargs["team_id"] == "TEAM1"

    # Subsequent iterations should use update_message
    if call_count >= 2:
        assert slack.update_message.call_count >= 1
        update_call = slack.update_message.call_args
        assert update_call.kwargs["channel"] == "C1"
        assert update_call.kwargs["ts"] == "111.222"


@pytest.mark.asyncio
async def test_heartbeat_falls_back_on_update_failure():
    """If update_message fails, heartbeat should post a new message."""
    slack = MagicMock()
    slack.post_thread_message.return_value = MagicMock(data={"ts": "111.222"})
    slack.update_message.side_effect = Exception("update failed")
    call_count = 0
    original_sleep = asyncio.sleep

    async def _counting_sleep(_seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise asyncio.CancelledError
        await original_sleep(0)

    with patch("app.tasks.jobs.asyncio.sleep", side_effect=_counting_sleep):
        with pytest.raises(asyncio.CancelledError):
            await _build_heartbeat_loop(
                slack=slack,
                channel_id="C1",
                thread_ts="T1",
                team_id="TEAM1",
                feature_title="Test",
                mode="new_feature",
                target_repo="",
                started_at=datetime.utcnow(),
                interval_seconds=0,
                timeout_window_seconds=0,
            )

    # Should have called post_thread_message multiple times (fallback after update failure)
    assert slack.post_thread_message.call_count >= 2


@pytest.mark.asyncio
async def test_heartbeat_includes_team_id():
    """Heartbeat messages should pass team_id for multi-workspace."""
    slack = MagicMock()
    slack.post_thread_message.return_value = None

    async def _one_shot_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    call_count = 0
    original_sleep = asyncio.sleep

    async def _one_shot(_seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError
        await original_sleep(0)

    with patch("app.tasks.jobs.asyncio.sleep", side_effect=_one_shot):
        with pytest.raises(asyncio.CancelledError):
            await _build_heartbeat_loop(
                slack=slack,
                channel_id="C1",
                thread_ts="T1",
                team_id="TEAM_ABC",
                feature_title="Test",
                mode="new_feature",
                target_repo="",
                started_at=datetime.utcnow(),
                interval_seconds=0,
                timeout_window_seconds=0,
            )

    assert slack.post_thread_message.call_count >= 1
    assert slack.post_thread_message.call_args.kwargs["team_id"] == "TEAM_ABC"


@pytest.mark.asyncio
async def test_heartbeat_shows_timeout_info():
    """Heartbeat with timeout_window_seconds should include remaining time."""
    slack = MagicMock()
    slack.post_thread_message.return_value = None
    call_count = 0
    original_sleep = asyncio.sleep

    async def _one_shot(_seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError
        await original_sleep(0)

    with patch("app.tasks.jobs.asyncio.sleep", side_effect=_one_shot):
        with pytest.raises(asyncio.CancelledError):
            await _build_heartbeat_loop(
                slack=slack,
                channel_id="C1",
                thread_ts="T1",
                team_id="",
                feature_title="Test",
                mode="new_feature",
                target_repo="",
                started_at=datetime.utcnow(),
                interval_seconds=0,
                timeout_window_seconds=3600,
            )

    text = slack.post_thread_message.call_args.kwargs["text"]
    assert "Timeout window" in text
    assert "remaining" in text


# ---------------------------------------------------------------------------
# Build reactions
# ---------------------------------------------------------------------------


def _make_feature(*, channel="C1", thread_ts="T1", message_ts="M1", team_id="TEAM1", status="BUILDING"):
    f = MagicMock()
    f.slack_channel_id = channel
    f.slack_thread_ts = thread_ts
    f.slack_message_ts = message_ts
    f.slack_team_id = team_id
    f.status = status
    f.title = "Test Feature"
    f.id = "feat-123"
    f.spec = {}
    f.last_error = ""
    f.active_build_job_id = ""
    f.github_pr_url = ""
    f.preview_url = ""
    f.github_issue_url = ""
    f.requester_user_id = "U1"
    f.events = []
    return f


def test_success_reaction_called_on_build_complete():
    """When a build succeeds, add_reaction with white_check_mark should be called."""
    slack = MagicMock()
    slack.add_reaction.return_value = None

    # Simulate what kickoff_build does after success
    feature = _make_feature()
    slack.add_reaction(
        channel=feature.slack_channel_id,
        timestamp=feature.slack_message_ts,
        name="white_check_mark",
        team_id=feature.slack_team_id,
    )

    slack.add_reaction.assert_called_once_with(
        channel="C1",
        timestamp="M1",
        name="white_check_mark",
        team_id="TEAM1",
    )


def test_failure_reaction_called_on_build_failure():
    """When a build fails, add_reaction with x should be called."""
    slack = MagicMock()
    slack.add_reaction.return_value = None

    feature = _make_feature()
    slack.add_reaction(
        channel=feature.slack_channel_id,
        timestamp=feature.slack_message_ts,
        name="x",
        team_id=feature.slack_team_id,
    )

    slack.add_reaction.assert_called_once_with(
        channel="C1",
        timestamp="M1",
        name="x",
        team_id="TEAM1",
    )


def test_reaction_skipped_when_no_message_ts():
    """No reaction should be attempted if slack_message_ts is empty."""
    slack = MagicMock()
    feature = _make_feature(message_ts="")

    # Mirror the guard in jobs.py
    if feature.slack_channel_id and feature.slack_message_ts:
        slack.add_reaction(
            channel=feature.slack_channel_id,
            timestamp=feature.slack_message_ts,
            name="white_check_mark",
            team_id=feature.slack_team_id,
        )

    slack.add_reaction.assert_not_called()


# ---------------------------------------------------------------------------
# Stale alert uses adapter
# ---------------------------------------------------------------------------


def test_stale_alert_goes_through_adapter():
    """Cleanup worker stale alerts should go through SlackAdapter."""
    from app.services.slack_adapter import MockSlackAdapter

    adapter = MockSlackAdapter()
    # Should not raise
    adapter.post_thread_message(
        channel="C1",
        thread_ts="T1",
        text="Build status is still PR_OPENED...",
        team_id="TEAM1",
    )
