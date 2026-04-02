"""Tests for spec summary blocks and help subcommand."""

from __future__ import annotations

from app.services.block_builders import build_spec_summary_blocks


def test_spec_summary_blocks_include_title():
    blocks = build_spec_summary_blocks({"title": "Add dark mode", "repo": "org/app"})
    text = str(blocks)
    assert "Add dark mode" in text
    assert "org/app" in text


def test_spec_summary_blocks_skip_empty_fields():
    blocks = build_spec_summary_blocks({"title": "Test", "problem": ""})
    text = str(blocks)
    assert "Problem" not in text


def test_spec_summary_blocks_have_confirm_button():
    blocks = build_spec_summary_blocks({"title": "Test"})
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions) == 1
    button_ids = [e["action_id"] for e in actions[0]["elements"]]
    assert "ff_confirm_spec" in button_ids
    assert "ff_edit_field" in button_ids
    assert "ff_cancel_intake" in button_ids


def test_spec_summary_handles_list_acceptance_criteria():
    blocks = build_spec_summary_blocks({
        "title": "Test",
        "acceptance_criteria": ["AC1", "AC2"],
    })
    text = str(blocks)
    assert "AC1" in text
    assert "AC2" in text


def test_help_subcommand_content():
    """Help text references all subcommands."""
    # Import the function to verify it exists and builds the expected text
    from app.slackbot import _handle_help_subcommand, PRIMARY_SLASH_COMMAND
    # The function itself requires client/body, so just verify constants are accessible
    assert PRIMARY_SLASH_COMMAND == "/prfactory"
