from __future__ import annotations

import logging
from types import SimpleNamespace

from app.config import Settings
import app.slackbot as slackbot_mod
from app.slackbot import (
    IntakeSession,
    _build_thread_history,
    _handle_frontier_escalation,
    _handle_model_intake_action,
    _post_thread_message_with_optional_model_context,
    _process_session_message,
    _start_create_intake,
    _thread_blocks_with_cost_summary,
)


class DummyClient:
    def __init__(self, thread_messages: list[dict[str, object]] | None = None):
        self.posted: list[dict[str, object]] = []
        self.ephemeral: list[dict[str, object]] = []
        self.thread_messages = thread_messages or []

    def chat_postMessage(self, **kwargs):
        self.posted.append(kwargs)
        return {"ok": True, "ts": "2.0"}

    def chat_postEphemeral(self, **kwargs):
        self.ephemeral.append(kwargs)
        return {"ok": True}

    def conversations_replies(self, **kwargs):
        return {"ok": True, "messages": list(self.thread_messages)}


def _settings(
    *,
    openrouter_api_key: str = "",
    openrouter_mini_model: str = "qwen/qwen3.5-9b",
    reviewer_allowed_users: str = "",
) -> Settings:
    return Settings.model_construct(
        openrouter_api_key=openrouter_api_key,
        openrouter_mini_model=openrouter_mini_model,
        openrouter_frontier_model="anthropic/claude-opus-4-6",
        reviewer_allowed_users=reviewer_allowed_users,
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
        queue=list(queue or ["title"]),
    )


def _action(**kwargs):
    defaults = {
        "action": "clarify",
        "field_name": None,
        "field_value": None,
        "next_question": None,
        "confidence": 0.9,
        "reasoning": "",
        "model": "",
        "user_skill": "technical",
        "suggested_repo": None,
        "suggested_branch": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _logger() -> logging.Logger:
    return logging.getLogger("tests.slackbot_intake_router")


def test_build_thread_history_maps_user_and_assistant_messages() -> None:
    history = _build_thread_history(
        [
            {"text": "User question", "user": "U123"},
            {"text": "Bot answer", "bot_id": "B123"},
            {"text": "Another bot answer", "subtype": "bot_message"},
            {"text": ""},
        ]
    )

    assert history == [
        {"role": "user", "content": "User question"},
        {"role": "assistant", "content": "Bot answer"},
        {"role": "assistant", "content": "Another bot answer"},
    ]


def test_uses_model_path_when_configured(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["title", "repo"])
    client = DummyClient(
        thread_messages=[
            {"text": "I need dark mode", "user": "U123"},
            {"text": "What should this request be titled?", "bot_id": "B123"},
        ]
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_handle_hardcoded_intake_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback should not be used")),
    )

    def fake_classify(*, message, conversation_history, current_fields, slack_user_id=""):
        captured["message"] = message
        captured["conversation_history"] = conversation_history
        captured["current_fields"] = current_fields
        captured["slack_user_id"] = slack_user_id
        return _action(
            action="ask_field",
            field_name="title",
            field_value="Add dark mode toggle",
            next_question="Which repo should I use?",
            model="qwen/qwen3.5-9b",
        )

    monkeypatch.setattr(slackbot_mod, "_classify_intake_message_sync", fake_classify)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "Dark mode toggle"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="Dark mode toggle",
        subtype=None,
    )

    assert captured["message"] == "Dark mode toggle"
    assert captured["conversation_history"] == [
        {"role": "user", "content": "I need dark mode"},
        {"role": "assistant", "content": "What should this request be titled?"},
    ]
    assert captured["current_fields"] == {}
    assert captured["slack_user_id"] == "U123"
    assert session.answers["title"] == "Add dark mode toggle"
    assert session.queue == ["repo"]
    assert client.posted[-1]["text"] == "Which repo should I use?"
    assert any("Assisted by qwen3.5-9b" in str(block) for block in client.posted[-1]["blocks"])


def test_falls_back_when_no_key(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="")
    session = _session()
    client = DummyClient()
    fallback_calls: list[object] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("classify should not run without a key")),
    )
    monkeypatch.setattr(slackbot_mod, "_handle_hardcoded_intake_message", lambda *args, **kwargs: fallback_calls.append(True))

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "Dark mode toggle"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="Dark mode toggle",
        subtype=None,
    )

    assert fallback_calls == [True]


def test_falls_back_when_no_module(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient()
    fallback_calls: list[object] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", False)
    monkeypatch.setattr(slackbot_mod, "_handle_hardcoded_intake_message", lambda *args, **kwargs: fallback_calls.append(True))

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "Dark mode toggle"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="Dark mode toggle",
        subtype=None,
    )

    assert fallback_calls == [True]


def test_falls_back_on_exception(monkeypatch, caplog) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient()
    fallback_calls: list[object] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("router exploded")),
    )
    monkeypatch.setattr(slackbot_mod, "_handle_hardcoded_intake_message", lambda *args, **kwargs: fallback_calls.append(True))

    with caplog.at_level(logging.ERROR):
        _process_session_message(
            client,
            _logger(),
            settings,
            session,
            event={"text": "Dark mode toggle"},
            user_id="U123",
            team_id="T123",
            channel_id="C123",
            thread_ts="1.0",
            text="Dark mode toggle",
            subtype=None,
        )

    assert fallback_calls == [True]
    assert "slack_model_intake_failed" in caplog.text


def test_confirm_action_finalizes_session(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["title"])
    client = DummyClient()
    finalized: list[tuple[object, object]] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: _action(action="confirm", field_name="title", field_value="Add dark mode"),
    )
    monkeypatch.setattr(slackbot_mod, "_finalize_session", lambda *args: finalized.append(args))
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "Yes, that looks right"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="Yes, that looks right",
        subtype=None,
    )

    assert session.answers["title"] == "Add dark mode"
    assert finalized


def test_clarify_action_posts_message(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: _action(
            action="clarify",
            next_question="Which settings page should this live on?",
            model="qwen/qwen3.5-9b",
        ),
    )

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "Dark mode toggle"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="Dark mode toggle",
        subtype=None,
    )

    assert client.posted[-1]["text"] == "Which settings page should this live on?"
    assert any("Assisted by qwen3.5-9b" in str(block) for block in client.posted[-1]["blocks"])


def test_cancel_action_drops_session(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient()
    dropped: list[object] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_classify_intake_message_sync", lambda **kwargs: _action(action="cancel"))
    monkeypatch.setattr(slackbot_mod, "_drop_session", lambda session_obj: dropped.append(session_obj))

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "Never mind"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="Never mind",
        subtype=None,
    )

    assert dropped == [session]
    assert "Intake cancelled" in str(client.posted[-1]["text"])


def test_escalate_action_posts_message(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: _action(action="escalate"),
    )

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "Can you figure it out?"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="Can you figure it out?",
        subtype=None,
    )

    assert client.posted[-1]["text"] == "This request needs a deeper look."
    assert any(block["type"] == "actions" for block in client.posted[-1]["blocks"])


def test_tier_indicator_shown_for_model_response() -> None:
    client = DummyClient()
    settings = _settings(openrouter_api_key="sk-test")

    _post_thread_message_with_optional_model_context(
        client,
        channel_id="C123",
        thread_ts="1.0",
        text="Which repo should I use?",
        settings=settings,
        tier="mini",
        model_name="qwen/qwen3.5-9b",
    )

    assert any("Assisted by qwen3.5-9b" in str(block) for block in client.posted[-1]["blocks"])


def test_no_tier_indicator_when_not_configured() -> None:
    client = DummyClient()
    settings = _settings(openrouter_api_key="")

    _post_thread_message_with_optional_model_context(
        client,
        channel_id="C123",
        thread_ts="1.0",
        text="Which repo should I use?",
        settings=settings,
        tier="mini",
        model_name="qwen/qwen3.5-9b",
    )

    assert "blocks" not in client.posted[-1]


def test_cost_context_block_shown_on_build_complete() -> None:
    events = [
        SimpleNamespace(data={"cost_usd": 0.0012, "tier": "mini", "model": "qwen/qwen3.5-9b"}),
        SimpleNamespace(data={"cost_usd": 0.0835, "tier": "frontier", "model": "anthropic/claude-opus-4-6"}),
        SimpleNamespace(data={"cost_usd": 0.0, "tier": "frontier", "model": "anthropic/claude-opus-4-6"}),
    ]

    blocks = _thread_blocks_with_cost_summary("PR step complete", events)

    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "section"
    assert any("LLM cost: $0.0847 (3 calls - 1 mini, 2 frontier)" in str(block) for block in blocks)


def test_no_cost_block_when_no_events() -> None:
    assert _thread_blocks_with_cost_summary("PR step complete", []) is None


def test_escalate_shows_two_buttons(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: _action(action="escalate", reasoning="Needs architectural review"),
    )

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "This is tricky"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="This is tricky",
        subtype=None,
    )

    actions = next(block for block in client.posted[-1]["blocks"] if block["type"] == "actions")
    action_ids = [element["action_id"] for element in actions["elements"]]
    assert action_ids == ["ff_escalate_frontier", "ff_escalate_human"]


def test_escalate_frontier_calls_frontier_model(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient(thread_messages=[{"text": "Please investigate", "user": "U123"}])
    observed: dict[str, object] = {}

    monkeypatch.setattr(slackbot_mod, "HAS_ESCALATE", True)

    def fake_escalate(**kwargs):
        observed.update(kwargs)
        return _action(
            action="clarify",
            next_question="The AI analyst needs one more detail.",
            model="anthropic/claude-opus-4-6",
        )

    monkeypatch.setattr(slackbot_mod, "_escalate_to_frontier_sync", fake_escalate)

    _handle_frontier_escalation(client, _logger(), settings, session, user_id="U123")

    assert observed["slack_user_id"] == "U123"
    assert observed["message"] == "Please investigate"
    assert client.posted[-1]["text"] == "The AI analyst needs one more detail."
    assert any("Analyzed by claude-opus-4-6" in str(block) for block in client.posted[-1]["blocks"])


def test_escalate_human_tags_channel(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test", reviewer_allowed_users="U999")
    session = _session()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    slackbot_mod._handle_human_escalation(client, settings, session)

    assert session.answers["_intake_paused_reason"] == "human"
    assert "<@U999>" in client.posted[-1]["text"]


def test_developer_skill_gets_plain_text(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session()
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: _action(
            action="clarify",
            next_question="Need the exact settings page.",
            model="qwen/qwen3.5-9b",
            user_skill="developer",
        ),
    )

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "dark mode"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="dark mode",
        subtype=None,
    )

    assert client.posted[-1]["text"] == "Need the exact settings page."
    assert "blocks" not in client.posted[-1]


def test_non_technical_skill_gets_help_context(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["repo"])
    client = DummyClient()

    monkeypatch.setattr(
        slackbot_mod,
        "_repo_options_for_slack",
        lambda *args, **kwargs: [{"text": {"type": "plain_text", "text": "acme/widgets"}, "value": "acme/widgets"}],
    )
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: _action(
            action="ask_field",
            field_name="repo",
            next_question="Which repository should this go in?",
            model="qwen/qwen3.5-9b",
            user_skill="non_technical",
        ),
    )

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "dark mode"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="dark mode",
        subtype=None,
    )

    assert any("A repository is where the code lives" in str(block) for block in client.posted[-1]["blocks"])


def test_user_id_passed_to_classifier(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_classify(**kwargs):
        captured.update(kwargs)
        return _action(action="clarify", next_question="Need one more detail.")

    monkeypatch.setattr(slackbot_mod, "classify_intake_message", fake_classify)

    action = slackbot_mod._classify_intake_message_sync(
        message="dark mode",
        conversation_history=[],
        current_fields={},
        slack_user_id="U123",
    )

    assert action.action == "clarify"
    assert captured["slack_user_id"] == "U123"


def test_user_id_skipped_on_old_signature(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_classify(*, message, conversation_history, current_fields):
        captured["message"] = message
        captured["conversation_history"] = conversation_history
        captured["current_fields"] = current_fields
        return _action(action="clarify", next_question="Need one more detail.")

    monkeypatch.setattr(slackbot_mod, "classify_intake_message", fake_classify)

    action = slackbot_mod._classify_intake_message_sync(
        message="dark mode",
        conversation_history=[],
        current_fields={},
        slack_user_id="U123",
    )

    assert action.action == "clarify"
    assert captured == {
        "message": "dark mode",
        "conversation_history": [],
        "current_fields": {},
    }


def test_special_fields_not_stored_as_feature_data(monkeypatch) -> None:
    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["repo"])
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: _action(action="ask_field", field_name="github_reauth"),
    )

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "help me connect", "user": "U123", "team": "T123"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="help me connect",
        subtype=None,
    )

    assert "github_reauth" not in session.answers
    assert "github_connect" not in session.answers
    assert session.answers["_waiting_for_github"] is True


# ---------------------------------------------------------------------------
# Model-aware /prfactory startup
# ---------------------------------------------------------------------------


def test_prfactory_with_seed_prompt_uses_model(monkeypatch) -> None:
    """When model is available and user typed '/prfactory I want dark mode',
    classify_intake_message is called with the seed prompt."""
    settings = _settings(openrouter_api_key="sk-test")
    client = DummyClient()
    captured: dict[str, object] = {}

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda session_obj: None)

    def fake_classify(*, message, conversation_history, current_fields, slack_user_id=""):
        captured["message"] = message
        captured["slack_user_id"] = slack_user_id
        return _action(
            action="ask_field",
            field_name="title",
            field_value="Add dark mode",
            next_question="Which repo should this go in?",
            model="qwen/qwen3.5-9b",
        )

    monkeypatch.setattr(slackbot_mod, "_classify_intake_message_sync", fake_classify)

    _start_create_intake(
        client,
        settings,
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        seed_prompt="I want to add dark mode to the settings page",
    )

    # Model was called with the seed prompt
    assert captured["message"] == "I want to add dark mode to the settings page"
    assert captured["slack_user_id"] == "U123"
    # Hardcoded title question ("How can I help you?") should NOT appear
    hardcoded_title_q = "How can I help you?"
    posted_texts = [str(p.get("text", "")) for p in client.posted]
    assert hardcoded_title_q not in posted_texts


def test_prfactory_without_seed_prompt_uses_model_greeting(monkeypatch) -> None:
    """When model is available and user typed just '/prfactory',
    an open-ended greeting is posted and _flow is set to 'model'."""
    settings = _settings(openrouter_api_key="sk-test")
    client = DummyClient()
    stored_sessions: list[IntakeSession] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda s: stored_sessions.append(s))

    _start_create_intake(
        client,
        settings,
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        seed_prompt="",
    )

    # Check the open-ended model greeting is posted (not the hardcoded title question)
    assert any("What would you like to build?" in str(p.get("text", "")) for p in client.posted)
    hardcoded_title_q = "How can I help you?"
    assert hardcoded_title_q not in [str(p.get("text", "")) for p in client.posted]
    # Session flow marker is "model"
    assert any(s.answers.get("_flow") == "model" for s in stored_sessions)


def test_prfactory_falls_back_on_model_error(monkeypatch, caplog) -> None:
    """When model classify raises, fall back to hardcoded flow."""
    settings = _settings(openrouter_api_key="sk-test")
    client = DummyClient()
    stored_sessions: list[IntakeSession] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda s: stored_sessions.append(s))
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("model exploded")),
    )
    monkeypatch.setattr(
        slackbot_mod,
        "_github_connection_context_block",
        lambda **kwargs: None,
    )

    with caplog.at_level(logging.ERROR):
        _start_create_intake(
            client,
            settings,
            team_id="T123",
            channel_id="C123",
            user_id="U123",
            seed_prompt="I want dark mode",
        )

    # Fell back to hardcoded — title question posted
    assert any("How can I help you?" in str(p.get("text", "")) or
               "What should this request be titled?" in str(p.get("text", ""))
               for p in client.posted)
    # Error logged
    assert "slack_model_intake_startup_failed" in caplog.text
    # Session flow switched to hardcoded
    assert any(s.answers.get("_flow") == "hardcoded" for s in stored_sessions)


def test_model_failure_shows_transition_message(monkeypatch, caplog) -> None:
    """In _process_session_message, model failure posts a transition message."""
    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["title"])
    session.answers["_flow"] = "model"
    client = DummyClient()
    fallback_calls: list[object] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(
        slackbot_mod,
        "_classify_intake_message_sync",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("model crash")),
    )
    monkeypatch.setattr(
        slackbot_mod,
        "_handle_hardcoded_intake_message",
        lambda *args, **kwargs: fallback_calls.append(True),
    )

    with caplog.at_level(logging.ERROR):
        _process_session_message(
            client,
            _logger(),
            settings,
            session,
            event={"text": "dark mode"},
            user_id="U123",
            team_id="T123",
            channel_id="C123",
            thread_ts="1.0",
            text="dark mode",
            subtype=None,
        )

    # Transition message posted
    assert any("trouble processing" in str(p.get("text", "")).lower() for p in client.posted)
    # Hardcoded fallback ran
    assert fallback_calls == [True]
    # Error logged
    assert "slack_model_intake_failed" in caplog.text


def test_seed_prompt_passed_to_model(monkeypatch) -> None:
    """The seed prompt is included as 'original_request' in current_fields."""
    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["title"])
    session.answers["_flow"] = "model"
    session.answers["_seed_prompt"] = "I want dark mode on the settings page"
    client = DummyClient(
        thread_messages=[{"text": "I want dark mode on the settings page", "user": "U123"}]
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda s: None)

    def fake_classify(*, message, conversation_history, current_fields, slack_user_id=""):
        captured["current_fields"] = current_fields
        return _action(
            action="ask_field",
            field_name="title",
            field_value="Add dark mode",
            next_question="Which repo?",
        )

    monkeypatch.setattr(slackbot_mod, "_classify_intake_message_sync", fake_classify)

    _process_session_message(
        client,
        _logger(),
        settings,
        session,
        event={"text": "yes that's right"},
        user_id="U123",
        team_id="T123",
        channel_id="C123",
        thread_ts="1.0",
        text="yes that's right",
        subtype=None,
    )

    assert captured["current_fields"]["original_request"] == "I want dark mode on the settings page"


def test_flow_marker_prevents_flip_flop(monkeypatch) -> None:
    """_flow='model' forces model path; _flow='hardcoded' forces hardcoded."""
    settings = _settings(openrouter_api_key="sk-test")
    client = DummyClient()

    # --- Model flow: classify IS called ---
    session_model = _session(queue=["title"])
    session_model.answers["_flow"] = "model"
    classify_called: list[bool] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda s: None)

    def fake_classify(**kwargs):
        classify_called.append(True)
        return _action(action="clarify", next_question="Need more detail.")

    monkeypatch.setattr(slackbot_mod, "_classify_intake_message_sync", fake_classify)

    _process_session_message(
        client, _logger(), settings, session_model,
        event={"text": "test"}, user_id="U123", team_id="T123",
        channel_id="C123", thread_ts="1.0", text="test", subtype=None,
    )
    assert classify_called == [True]

    # --- Hardcoded flow: classify is NOT called ---
    session_hardcoded = _session(queue=["title"])
    session_hardcoded.answers["_flow"] = "hardcoded"
    classify_called.clear()
    fallback_calls: list[bool] = []
    monkeypatch.setattr(
        slackbot_mod,
        "_handle_hardcoded_intake_message",
        lambda *args, **kwargs: fallback_calls.append(True),
    )

    _process_session_message(
        client, _logger(), settings, session_hardcoded,
        event={"text": "test"}, user_id="U123", team_id="T123",
        channel_id="C123", thread_ts="1.0", text="test", subtype=None,
    )
    assert classify_called == []  # Model was NOT called
    assert fallback_calls == [True]


def test_prfactory_hardcoded_when_no_model(monkeypatch) -> None:
    """When model is not available, /prfactory uses the hardcoded flow."""
    settings = _settings(openrouter_api_key="")
    client = DummyClient()
    stored_sessions: list[IntakeSession] = []

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda s: stored_sessions.append(s))
    monkeypatch.setattr(
        slackbot_mod,
        "_github_connection_context_block",
        lambda **kwargs: None,
    )

    _start_create_intake(
        client,
        settings,
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        seed_prompt="",
    )

    # Hardcoded title question posted
    assert any("How can I help you?" in str(p.get("text", "")) for p in client.posted)
    # Flow is hardcoded
    assert any(s.answers.get("_flow") == "hardcoded" for s in stored_sessions)


# ---------------------------------------------------------------------------
# Bug fix tests: repo loop, branch step, installation hint, shortcuts
# ---------------------------------------------------------------------------


def test_duplicate_repo_ask_skipped(monkeypatch) -> None:
    """When model asks for repo but session already has one, skip to next field."""
    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["repo", "base_branch"])
    session.answers["repo"] = "org/app"
    session.answers["_flow"] = "model"
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "HAS_INTAKE_ROUTER", True)
    monkeypatch.setattr(slackbot_mod, "_store_session", lambda s: None)

    # Model asks for repo again
    result = _handle_model_intake_action(
        client,
        settings,
        session,
        event={"text": "yes that one", "user": "U123", "team": "T123"},
        action=_action(action="ask_field", field_name="repo"),
    )

    assert result is True
    # Repo should NOT be re-asked — it's already collected
    assert "repo" not in session.queue


def test_repo_button_click_advances_to_branch(monkeypatch) -> None:
    """After _apply_repo_selection sets the repo, branch is the next field."""
    from app.slackbot import _apply_repo_selection

    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["repo", "base_branch"])
    session.answers["_flow"] = "model"
    session.answers["_intake_mode"] = "developer"
    client = DummyClient()

    monkeypatch.setattr(slackbot_mod, "_store_session", lambda s: None)
    monkeypatch.setattr(
        slackbot_mod,
        "_fetch_default_branch_for_repo",
        lambda *a, **kw: "main",
    )
    monkeypatch.setattr(
        slackbot_mod,
        "_fetch_branches_for_repo",
        lambda *a, **kw: ["main", "develop"],
    )

    _apply_repo_selection(
        client,
        settings,
        session,
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        selected="TreyMangat/github_indexer",
    )

    assert session.answers["repo"] == "TreyMangat/github_indexer"
    assert "repo" not in session.queue


def test_few_repos_shows_installation_hint(monkeypatch) -> None:
    """When repo dropdown has <= 1 real repo, show installation hint."""
    from app.slackbot import _build_repo_select_blocks

    settings = _settings(openrouter_api_key="sk-test")
    session = _session(queue=["repo"])

    monkeypatch.setattr(
        slackbot_mod,
        "_repo_options_for_slack",
        lambda *a, **kw: [
            {"text": {"type": "plain_text", "text": "None (use defaults)"}, "value": "__NONE__"},
            {"text": {"type": "plain_text", "text": "New repo (I will type it)"}, "value": "__NEW__"},
            {"text": {"type": "plain_text", "text": "TreyMangat/github_indexer"}, "value": "TreyMangat/github_indexer"},
        ],
    )

    blocks = _build_repo_select_blocks(
        settings,
        session,
        question="Pick a repo",
        suggested=None,
        user_skill="technical",
    )

    # Should have a context block with the installation hint
    context_blocks = [b for b in blocks if b.get("type") == "context"]
    hint_texts = [str(b) for b in context_blocks]
    assert any("Add more repos" in t for t in hint_texts)


def test_branch_selection_after_repo_in_model_flow(monkeypatch) -> None:
    """In model flow, base_branch stays in queue when repo is set."""
    from app.slackbot import _next_field

    session = _session(queue=["base_branch"])
    session.answers["_flow"] = "model"
    session.answers["repo"] = "org/app"

    field = _next_field(session)
    assert field == "base_branch"


def test_branch_skipped_in_normal_mode_without_developer() -> None:
    """In normal mode (non-model, non-developer), base_branch is skipped."""
    from app.slackbot import _next_field

    session = _session(queue=["base_branch"])
    session.answers["repo"] = "org/app"
    # No _flow=model, no developer mode

    field = _next_field(session)
    assert field == ""  # base_branch was skipped


def test_shortcut_phrase_in_prompt() -> None:
    """The intake prompt includes shortcut phrases section."""
    from app.services.intake_prompts import build_intake_system_prompt

    prompt = build_intake_system_prompt()
    assert "SHORTCUT PHRASES:" in prompt
    assert "just build it" in prompt.lower()
    assert "ship it" in prompt.lower()
    assert 'action="confirm"' in prompt
