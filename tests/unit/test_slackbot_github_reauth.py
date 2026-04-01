from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
import app.slackbot as slackbot_mod
from app.slackbot import (
    IntakeSession,
    _handle_model_intake_action,
    _process_session_message,
    _show_repo_dropdown_message,
    _start_create_intake,
)
import app.services.branch_catalog as branch_catalog_mod
from app.services.branch_catalog import (
    GitHubAuthError,
    build_github_oauth_url,
)


class DummyClient:
    def __init__(self):
        self.posted: list[dict[str, object]] = []
        self.ephemeral: list[dict[str, object]] = []
        self._ts = 1

    def chat_postMessage(self, **kwargs):
        payload = dict(kwargs)
        if "ts" not in payload:
            payload["ts"] = f"{self._ts}.0"
            self._ts += 1
        self.posted.append(payload)
        return {"ok": True, "ts": payload["ts"]}

    def chat_postEphemeral(self, **kwargs):
        self.ephemeral.append(dict(kwargs))
        return {"ok": True}

    def conversations_replies(self, **kwargs):
        return {"ok": True, "messages": []}


def _settings(*, base_url: str = "https://prfactory.example") -> Settings:
    return Settings.model_construct(
        base_url=base_url,
        openrouter_api_key="sk-test",
        openrouter_mini_model="qwen/qwen3.5-9b",
        openrouter_frontier_model="anthropic/claude-opus-4-6",
        enable_github_user_oauth=True,
        github_oauth_client_id="github-client",
        github_oauth_client_secret="github-secret",
        mock_mode=True,
    )


def _session(*, queue: list[str] | None = None) -> IntakeSession:
    return IntakeSession(
        mode="create",
        feature_id="",
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        message_ts="1.0",
        queue=list(queue or ["repo"]),
        answers={},
    )


def _action(**kwargs):
    defaults = {
        "action": "ask_field",
        "field_name": None,
        "field_value": None,
        "next_question": None,
        "confidence": 0.9,
        "reasoning": "",
        "model": "qwen/qwen3.5-9b",
        "user_skill": "technical",
        "suggested_repo": None,
        "suggested_branch": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_github_reauth_shows_button(monkeypatch) -> None:
    settings = _settings()
    session = _session()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    handled = _handle_model_intake_action(
        client,
        settings,
        session,
        event={"user": "U123", "team": "T123"},
        action=_action(field_name="github_reauth"),
    )

    assert handled is True
    assert session.answers["_waiting_for_github"] is True
    actions = next(block for block in client.posted[-1]["blocks"] if block["type"] == "actions")
    assert actions["elements"][0]["action_id"] == "ff_github_reauth"


def test_github_connect_shows_button(monkeypatch) -> None:
    settings = _settings()
    session = _session()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    handled = _handle_model_intake_action(
        client,
        settings,
        session,
        event={"user": "U123", "team": "T123"},
        action=_action(field_name="github_connect"),
    )

    assert handled is True
    assert session.answers["_waiting_for_github"] is True
    actions = next(block for block in client.posted[-1]["blocks"] if block["type"] == "actions")
    assert actions["elements"][0]["action_id"] == "ff_github_connect"


def test_session_not_dropped_on_reauth(monkeypatch) -> None:
    settings = _settings()
    session = _session()
    client = DummyClient()
    dropped: list[object] = []

    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    monkeypatch.setattr(slackbot_mod, "_drop_session", lambda session_obj: dropped.append(session_obj))

    _handle_model_intake_action(
        client,
        settings,
        session,
        event={"user": "U123", "team": "T123"},
        action=_action(field_name="github_reauth"),
    )

    assert dropped == []
    assert session.answers["_waiting_for_github"] is True


def test_resume_after_reauth_shows_repos(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    session.answers["_waiting_for_github"] = True
    session.answers["_waiting_for_github_mode"] = "reauth"
    client = DummyClient()
    captured: dict[str, object] = {}

    monkeypatch.setattr(slackbot_mod, "HAS_GITHUB_CONNECTION_CHECKER", True)
    monkeypatch.setattr(slackbot_mod, "check_github_connection", object())
    monkeypatch.setattr(
        slackbot_mod,
        "_github_connection_snapshot_sync",
        lambda **kwargs: {"status": "connected", "username": "octocat"},
    )
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    def fake_show_repo_dropdown(client_obj, settings_obj, session_obj, *, question, suggested=None, user_skill="technical", model_name=""):
        captured["question"] = question
        captured["user_skill"] = user_skill
        captured["model_name"] = model_name

    monkeypatch.setattr(slackbot_mod, "_show_repo_dropdown_message", fake_show_repo_dropdown)

    _process_session_message(
        client,
        slackbot_mod.module_logger,
        settings,
        session,
        event={"text": "I reconnected", "user": "U123", "team": "T123"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="I reconnected",
        subtype=None,
    )

    assert "_waiting_for_github" not in session.answers
    assert captured["question"] == "GitHub reconnected! Here are your repos:"


def test_still_waiting_shows_reminder(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    session.answers["_waiting_for_github"] = True
    session.answers["_waiting_for_github_mode"] = "reauth"
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_GITHUB_CONNECTION_CHECKER", True)
    monkeypatch.setattr(slackbot_mod, "check_github_connection", object())
    monkeypatch.setattr(
        slackbot_mod,
        "_github_connection_snapshot_sync",
        lambda **kwargs: {"status": "expired", "username": "octocat"},
    )

    _process_session_message(
        client,
        slackbot_mod.module_logger,
        settings,
        session,
        event={"text": "still here", "user": "U123", "team": "T123"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="still here",
        subtype=None,
    )

    assert session.answers["_waiting_for_github"] is True
    assert "Still waiting for GitHub connection" in str(client.posted[-1]["text"])


def test_manual_repo_entry_during_wait(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    session.answers["_waiting_for_github"] = True
    session.answers["_waiting_for_github_mode"] = "connect"
    client = DummyClient()
    finalized: list[object] = []

    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    monkeypatch.setattr(slackbot_mod, "_finalize_session", lambda *args: finalized.append(True))

    _process_session_message(
        client,
        slackbot_mod.module_logger,
        settings,
        session,
        event={"text": "my-org/my-repo", "user": "U123", "team": "T123"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="my-org/my-repo",
        subtype=None,
    )

    assert session.answers["repo"] == "my-org/my-repo"
    assert "_waiting_for_github" not in session.answers
    assert finalized == [True]


def test_oauth_url_built_correctly(monkeypatch) -> None:
    settings = _settings(base_url="https://app.example")

    monkeypatch.setattr(branch_catalog_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(branch_catalog_mod, "github_connect_url_for_user", lambda *_args, **_kwargs: "")

    url = build_github_oauth_url("U123", "T123")

    assert url.startswith("https://app.example/api/github/install?")
    assert "slack_user_id=U123" in url
    assert "slack_team_id=T123" in url


def test_dropdown_401_shows_reauth(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    client = DummyClient()

    monkeypatch.setattr(
        slackbot_mod,
        "_build_repo_select_blocks",
        lambda *args, **kwargs: (_ for _ in ()).throw(GitHubAuthError("expired")),
    )
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    _show_repo_dropdown_message(
        client,
        settings,
        session,
        question="Which repository should this go in?",
        suggested=None,
        user_skill="technical",
        model_name="qwen/qwen3.5-9b",
    )

    actions = next(block for block in client.posted[-1]["blocks"] if block["type"] == "actions")
    assert actions["elements"][0]["action_id"] == "ff_github_reauth"


def test_connected_status_shown_at_intake_start(monkeypatch) -> None:
    settings = _settings()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", False)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    monkeypatch.setattr(
        slackbot_mod,
        "_github_connection_context_block",
        lambda **kwargs: {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": ":white_check_mark: _GitHub connected as @octocat_"}],
        },
    )

    _start_create_intake(
        client,
        settings,
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        seed_prompt="Add dark mode",
    )

    assert any("GitHub connected as @octocat" in str(block) for block in client.posted[1]["blocks"])


def test_expired_status_shown_at_intake_start(monkeypatch) -> None:
    settings = _settings()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", False)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    monkeypatch.setattr(
        slackbot_mod,
        "_github_connection_context_block",
        lambda **kwargs: {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": ":warning: _GitHub token expired - you'll be prompted to reconnect_"}],
        },
    )

    _start_create_intake(
        client,
        settings,
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        seed_prompt="Add dark mode",
    )

    assert any("GitHub token expired" in str(block) for block in client.posted[1]["blocks"])
