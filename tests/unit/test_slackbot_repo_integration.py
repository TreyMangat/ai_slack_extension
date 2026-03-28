from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
import app.slackbot as slackbot_mod
from app.slackbot import IntakeSession, _apply_repo_selection, _handle_model_intake_action, _show_repo_dropdown_message


class DummyClient:
    def __init__(self):
        self.posted: list[dict[str, object]] = []
        self.ephemeral: list[dict[str, object]] = []

    def chat_postMessage(self, **kwargs):
        self.posted.append(kwargs)
        return {"ok": True, "ts": "2.0"}

    def chat_postEphemeral(self, **kwargs):
        self.ephemeral.append(kwargs)
        return {"ok": True}


def _settings() -> Settings:
    return Settings.model_construct(
        openrouter_api_key="sk-test",
        openrouter_mini_model="qwen/qwen3.5-9b",
        openrouter_frontier_model="anthropic/claude-opus-4-6",
        github_enabled=False,
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
        queue=list(queue or []),
        answers={},
    )


def _action(**kwargs):
    defaults = {
        "action": "ask_field",
        "field_name": "repo",
        "field_value": None,
        "next_question": "Which repository should this go in?",
        "confidence": 0.9,
        "reasoning": "",
        "model": "qwen/qwen3.5-9b",
        "user_skill": "technical",
        "suggested_repo": None,
        "suggested_branch": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_repo_dropdown_shown_for_repo_field(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    client = DummyClient()

    monkeypatch.setattr(
        slackbot_mod,
        "_repo_options_for_slack",
        lambda *args, **kwargs: [{"text": {"type": "plain_text", "text": "acme/widgets"}, "value": "acme/widgets"}],
    )
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    handled = _handle_model_intake_action(
        client,
        settings,
        session,
        event={"text": "Use widgets", "files": []},
        action=_action(field_name="repo", suggested_repo=None),
    )

    assert handled is True
    accessory = client.posted[-1]["blocks"][0]["accessory"]
    assert accessory["action_id"] == "ff_repo_select"
    assert accessory["options"][0]["value"] == "acme/widgets"


def test_suggested_repo_shown_as_button(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    client = DummyClient()

    monkeypatch.setattr(
        slackbot_mod,
        "_repo_options_for_slack",
        lambda *args, **kwargs: [{"text": {"type": "plain_text", "text": "acme/widgets"}, "value": "acme/widgets"}],
    )
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    _handle_model_intake_action(
        client,
        settings,
        session,
        event={"text": "Use widgets", "files": []},
        action=_action(field_name="repo", suggested_repo="acme/widgets"),
    )

    action_blocks = [block for block in client.posted[-1]["blocks"] if block["type"] == "actions"]
    assert any(element["action_id"] == "ff_accept_repo_suggestion" for element in action_blocks[0]["elements"])


def test_accept_repo_suggestion_updates_session(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    client = DummyClient()
    finalized: list[object] = []

    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    monkeypatch.setattr(slackbot_mod, "_finalize_create_session", lambda *args: finalized.append(True))

    _apply_repo_selection(
        client,
        settings,
        session,
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        selected="acme/widgets",
    )

    assert session.answers["repo"] == "acme/widgets"
    assert finalized == [True]


def test_show_repo_dropdown_falls_back_to_existing(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["repo"])
    client = DummyClient()
    captured: dict[str, object] = {}

    def fake_repo_options(settings_obj, *, user_id, team_id, query):
        captured["user_id"] = user_id
        captured["team_id"] = team_id
        captured["query"] = query
        return [{"text": {"type": "plain_text", "text": "acme/widgets"}, "value": "acme/widgets"}]

    monkeypatch.setattr(slackbot_mod, "_repo_options_for_slack", fake_repo_options)
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

    assert captured == {"user_id": "U123", "team_id": "T123", "query": ""}
    assert client.posted[-1]["blocks"][0]["accessory"]["action_id"] == "ff_repo_select"


def test_branch_dropdown_shown_for_branch_field(monkeypatch) -> None:
    settings = _settings()
    session = _session(queue=["base_branch"])
    session.answers["repo"] = "acme/widgets"
    client = DummyClient()

    monkeypatch.setattr(
        slackbot_mod,
        "_branch_options_for_slack",
        lambda *args, **kwargs: [{"text": {"type": "plain_text", "text": "main"}, "value": "main"}],
    )
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    handled = _handle_model_intake_action(
        client,
        settings,
        session,
        event={"text": "Use main", "files": []},
        action=_action(field_name="branch", suggested_branch="main", next_question="Which branch should we use?"),
    )

    assert handled is True
    accessory = client.posted[-1]["blocks"][0]["accessory"]
    assert accessory["action_id"] == "ff_branch_select"
    assert accessory["options"][0]["value"] == "main"
