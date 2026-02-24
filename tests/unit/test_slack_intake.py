from __future__ import annotations

from app.slackbot import IntakeSession, _build_create_queue, _capture_field_answer


def _dummy_session() -> IntakeSession:
    return IntakeSession(
        mode="create",
        feature_id="",
        user_id="U123",
        channel_id="C123",
        thread_ts="1.0",
        message_ts="1.0",
    )


def test_build_create_queue_excludes_repo_when_not_required() -> None:
    queue = _build_create_queue(has_title=False, require_repo=False)
    assert "repo" not in queue


def test_build_create_queue_includes_repo_when_required() -> None:
    queue = _build_create_queue(has_title=False, require_repo=True)
    assert "repo" in queue


def test_capture_repo_requires_value_when_required() -> None:
    session = _dummy_session()
    ok, note = _capture_field_answer(
        session,
        field="repo",
        event={"text": "skip"},
        require_repo=True,
    )
    assert ok is False
    assert "required" in note.lower()


def test_capture_repo_accepts_org_repo() -> None:
    session = _dummy_session()
    ok, _note = _capture_field_answer(
        session,
        field="repo",
        event={"text": "acme/widgets"},
        require_repo=True,
    )
    assert ok is True
    assert session.answers["repo"] == "acme/widgets"
