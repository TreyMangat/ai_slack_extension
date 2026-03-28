from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import app.cleanup_worker as cleanup_worker


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value

    def scalar_one(self):
        return self._value


class _FakeDB:
    def __init__(self, responses: list[object]):
        self._responses = list(responses)

    def execute(self, _query):
        if not self._responses:
            raise AssertionError("unexpected db.execute call")
        return _FakeResult(self._responses.pop(0))


@dataclass
class _FakeFeature:
    id: str
    status: str
    title: str
    updated_at: datetime
    slack_channel_id: str
    slack_thread_ts: str
    slack_team_id: str


class _FakeSlack:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def post_thread_message(self, *, channel: str, thread_ts: str, text: str, team_id: str = "") -> None:
        self.messages.append(
            {
                "channel": channel,
                "thread_ts": thread_ts,
                "text": text,
                "team_id": team_id,
            }
        )


def _fake_settings():
    class _Settings:
        callback_stale_alert_minutes = 30
        callback_stale_check_max_per_run = 50

    return _Settings()


def test_stale_callback_alert_is_emitted_only_once(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    feature = _FakeFeature(
        id="feature-1",
        status="PR_OPENED",
        title="Test feature",
        updated_at=now - timedelta(hours=2),
        slack_channel_id="C123",
        slack_thread_ts="123.456",
        slack_team_id="T123",
    )

    db_runs = [
        _FakeDB([[feature], 0, 0]),
        _FakeDB([[feature], 1, 0]),
    ]

    @contextmanager
    def _db_session():
        if not db_runs:
            raise AssertionError("unexpected db session call")
        yield db_runs.pop(0)

    logged_events: list[dict[str, str]] = []
    slack = _FakeSlack()

    monkeypatch.setattr(cleanup_worker, "db_session", _db_session)
    monkeypatch.setattr(cleanup_worker, "get_settings", _fake_settings)
    monkeypatch.setattr(cleanup_worker, "get_slack_adapter", lambda: slack)
    monkeypatch.setattr(
        cleanup_worker,
        "log_event",
        lambda _db, feature, **kwargs: logged_events.append({"feature_id": feature.id, "event_type": kwargs["event_type"]}),
    )

    cleanup_worker.run_stale_callback_alerts_once()
    cleanup_worker.run_stale_callback_alerts_once()

    assert len(logged_events) == 1
    assert logged_events[0]["event_type"] == cleanup_worker.CALLBACK_STALE_ALERTED_EVENT
    assert len(slack.messages) == 1


def test_stale_callback_alert_skips_when_disabled(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    feature = _FakeFeature(
        id="feature-2",
        status="PR_OPENED",
        title="Disabled feature",
        updated_at=now - timedelta(hours=2),
        slack_channel_id="C123",
        slack_thread_ts="123.789",
        slack_team_id="T123",
    )

    @contextmanager
    def _db_session():
        yield _FakeDB([[feature], 0, 1])

    logged_events: list[dict[str, str]] = []
    slack = _FakeSlack()

    monkeypatch.setattr(cleanup_worker, "db_session", _db_session)
    monkeypatch.setattr(cleanup_worker, "get_settings", _fake_settings)
    monkeypatch.setattr(cleanup_worker, "get_slack_adapter", lambda: slack)
    monkeypatch.setattr(
        cleanup_worker,
        "log_event",
        lambda _db, feature, **kwargs: logged_events.append({"feature_id": feature.id, "event_type": kwargs["event_type"]}),
    )

    cleanup_worker.run_stale_callback_alerts_once()

    assert logged_events == []
    assert slack.messages == []
