"""Tests for spec summary blocks and help subcommand."""

from __future__ import annotations

from app.services.block_builders import build_spec_summary_blocks


def test_spec_summary_blocks_include_title():
    blocks = build_spec_summary_blocks({"title": "Add dark mode", "repo": "org/app"})
    text = str(blocks)
    assert "Add dark mode" in text


def test_spec_summary_blocks_skip_empty():
    blocks = build_spec_summary_blocks({"title": "Test", "problem": ""})
    text = str(blocks)
    assert "Problem" not in text


def test_spec_summary_has_confirm_button():
    blocks = build_spec_summary_blocks({"title": "Test"})
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions) == 1
    button_ids = [e["action_id"] for e in actions[0]["elements"]]
    assert "ff_confirm_spec" in button_ids
    assert "ff_edit_field" in button_ids
    assert "ff_cancel_intake" in button_ids


def test_spec_summary_handles_list_criteria():
    blocks = build_spec_summary_blocks({
        "title": "Test",
        "acceptance_criteria": ["AC1", "AC2"],
    })
    text = str(blocks)
    assert "AC1" in text
    assert "AC2" in text


def test_help_subcommand_content():
    from app.slackbot import _handle_help_subcommand, PRIMARY_SLASH_COMMAND

    posted: list[dict[str, str]] = []

    class DummyClient:
        def chat_postEphemeral(self, **kwargs):
            posted.append(kwargs)

    _handle_help_subcommand(
        {"user_id": "U123", "channel_id": "C123"},
        DummyClient(),
        settings=None,
    )

    assert len(posted) == 1
    text = posted[0]["text"]
    assert f"{PRIMARY_SLASH_COMMAND} help" in text
    assert f"{PRIMARY_SLASH_COMMAND} status" in text
    assert f"{PRIMARY_SLASH_COMMAND} cost" in text
    assert "confirm, edit, or cancel" in text.lower()
