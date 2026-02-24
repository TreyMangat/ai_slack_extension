from __future__ import annotations

from app.config import Settings
import app.slackbot as slackbot_mod
from app.slackbot import (
    INTAKE_MODE_DEVELOPER,
    IntakeSession,
    _build_create_queue,
    _capture_field_answer,
    _create_spec_from_session,
    _developer_mode_repo_blocks,
    _fallback_branch_options,
    _intake_mode_toggle_label,
    _next_field,
    _repo_selection_mutable,
    _repo_options_for_slack,
    _set_session_intake_mode,
    _title_prompt_blocks,
)


def _dummy_session() -> IntakeSession:
    return IntakeSession(
        mode="create",
        feature_id="",
        user_id="U123",
        team_id="T123",
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
    assert "base_branch" in queue


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


def test_next_field_skips_base_branch_in_normal_mode() -> None:
    session = _dummy_session()
    session.queue = ["repo", "base_branch"]
    session.answers["repo"] = "acme/widgets"
    session.queue.pop(0)
    assert _next_field(session) == ""


def test_next_field_keeps_base_branch_in_developer_mode() -> None:
    session = _dummy_session()
    _set_session_intake_mode(session, INTAKE_MODE_DEVELOPER)
    session.queue = ["base_branch"]
    session.answers["repo"] = "acme/widgets"
    assert _next_field(session) == "base_branch"


def test_capture_base_branch_validates_name() -> None:
    session = _dummy_session()
    ok, _ = _capture_field_answer(session, field="base_branch", event={"text": "main"})
    assert ok is True
    assert session.answers["base_branch"] == "main"


def test_create_spec_from_session_autofills_problem_and_acceptance_defaults() -> None:
    session = _dummy_session()
    session.answers = {
        "title": "Add invoice export",
        "repo": "acme/widgets",
    }
    spec = _create_spec_from_session(session)
    assert spec["problem"] == "Add invoice export"
    assert spec["business_justification"]
    assert len(spec["acceptance_criteria"]) >= 1


def test_title_prompt_blocks_include_visible_question_and_hint() -> None:
    blocks = _title_prompt_blocks(mode="normal")
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == "How can I help you?"
    assert blocks[1]["type"] == "context"
    assert "Enter what you want to build" in blocks[1]["elements"][0]["text"]


def test_repo_options_prompt_reconnect_when_saved_connection_missing_token(monkeypatch) -> None:
    settings = Settings.model_construct(
        enable_github_user_oauth=True,
        github_oauth_client_id="cid",
        github_oauth_client_secret="secret",
        github_repo_owner="",
        github_repo_name="",
    )
    monkeypatch.setattr(slackbot_mod, "_resolve_github_user_token", lambda **_: "")
    monkeypatch.setattr(slackbot_mod, "has_github_user_connection", lambda **_: True)
    monkeypatch.setattr(slackbot_mod, "_fetch_repositories_for_user", lambda *_, **__: [])

    options = _repo_options_for_slack(
        settings,
        user_id="U123",
        team_id="T123",
        query="",
    )

    assert any(opt["value"] == "__CONNECT__" for opt in options)
    assert any("Reconnect GitHub" in opt["text"]["text"] for opt in options)


def test_developer_repo_blocks_use_static_select_options() -> None:
    options = [
        {"text": {"type": "plain_text", "text": "one"}, "value": "one/repo"},
        {"text": {"type": "plain_text", "text": "two"}, "value": "two/repo"},
    ]
    blocks = _developer_mode_repo_blocks(options=options)
    accessory = blocks[0]["accessory"]
    assert accessory["type"] == "static_select"
    assert len(accessory["options"]) == 2


def test_fallback_branch_options_prompts_for_existing_branch_name() -> None:
    options = _fallback_branch_options()
    labels = [opt["text"]["text"] for opt in options]
    assert "Type branch name" in labels


def test_intake_mode_toggle_button_labels_show_action() -> None:
    assert _intake_mode_toggle_label("normal") == "Switch to Developer"
    assert _intake_mode_toggle_label("developer") == "Switch to Normal"


def test_repo_selection_mutable_true_when_repo_is_current_field() -> None:
    session = _dummy_session()
    _set_session_intake_mode(session, INTAKE_MODE_DEVELOPER)
    session.queue = ["repo", "base_branch"]
    assert _repo_selection_mutable(session) is True


def test_repo_selection_mutable_true_when_base_branch_pending_in_developer_mode() -> None:
    session = _dummy_session()
    _set_session_intake_mode(session, INTAKE_MODE_DEVELOPER)
    session.queue = ["base_branch"]
    session.answers["repo"] = "acme/widgets"
    assert _repo_selection_mutable(session) is True


def test_repo_selection_mutable_false_after_repo_and_branch_complete() -> None:
    session = _dummy_session()
    _set_session_intake_mode(session, INTAKE_MODE_DEVELOPER)
    session.queue = []
    session.answers["repo"] = "acme/widgets"
    session.answers["base_branch"] = "main"
    assert _repo_selection_mutable(session) is False
