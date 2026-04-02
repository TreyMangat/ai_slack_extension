"""Tests for spec quality — no parroted AC, no title=problem duplication."""
from __future__ import annotations

from app.services.intake_helpers import create_spec_from_session
from app.services.intake_session import IntakeSession


def test_empty_ac_not_auto_generated():
    """When user provides no AC, spec should have empty list, not junk."""
    session = IntakeSession(
        mode="create",
        feature_id="",
        user_id="U1",
        team_id="T1",
        channel_id="C1",
        thread_ts="123",
        message_ts="123",
        queue=[],
        answers={
            "title": "Add dark mode",
            "problem": "Users want dark mode in settings",
            "repo": "org/app",
        },
    )
    spec = create_spec_from_session(session)
    ac = spec.get("acceptance_criteria", [])
    assert ac == []
    for item in ac:
        assert "Implements requested behavior" not in item, f"Auto-generated junk AC found: {item}"
        assert "committed and opened as a PR" not in item.lower(), f"Generic boilerplate AC found: {item}"


def test_title_and_problem_are_different():
    """Title should not be a copy of problem."""
    session = IntakeSession(
        mode="create",
        feature_id="",
        user_id="U1",
        team_id="T1",
        channel_id="C1",
        thread_ts="123",
        message_ts="123",
        queue=[],
        answers={
            "title": "Add dark mode toggle",
            "problem": "I want to add a dark mode toggle to the settings page",
            "repo": "org/app",
        },
    )
    spec = create_spec_from_session(session)
    if spec.get("title") and spec.get("problem"):
        assert spec["title"] != spec["problem"], "Title and problem should be different"


def test_no_ac_does_not_block_validation():
    """Empty acceptance_criteria should not make spec invalid."""
    from app.services.spec_validator import validate_spec

    spec = {
        "title": "Add dark mode",
        "problem": "Users want dark mode",
        "repo": "org/app",
        "acceptance_criteria": [],
    }
    is_valid, missing, _ = validate_spec(spec)
    assert is_valid is True, f"Spec should be valid without AC. Missing: {missing}"
    assert "acceptance_criteria" not in missing


def test_user_provided_ac_preserved():
    """If user explicitly provides AC, keep them as-is."""
    session = IntakeSession(
        mode="create",
        feature_id="",
        user_id="U1",
        team_id="T1",
        channel_id="C1",
        thread_ts="123",
        message_ts="123",
        queue=[],
        answers={
            "title": "Add dark mode",
            "repo": "org/app",
            "acceptance_criteria": "Toggle in settings\nPersists preference",
        },
    )
    spec = create_spec_from_session(session)
    ac = spec.get("acceptance_criteria", [])
    assert "Toggle in settings" in ac
    assert "Persists preference" in ac
