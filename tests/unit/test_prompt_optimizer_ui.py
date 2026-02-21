from __future__ import annotations

from app.services.prompt_optimizer import attach_optimized_prompt, detect_ui_feature


def test_detect_ui_feature_matches_keywords() -> None:
    spec = {
        "title": "Add UI dashboard page",
        "problem": "Need a frontend button and layout update",
        "business_justification": "Reviewers need a visual demo quickly",
        "acceptance_criteria": ["Dashboard page renders", "Button click shows modal"],
    }
    is_ui, keywords = detect_ui_feature(spec)
    assert is_ui is True
    assert "ui" in keywords or "frontend" in keywords


def test_attach_optimized_prompt_adds_ui_metadata() -> None:
    spec = {
        "title": "Build website landing page",
        "problem": "Need a simple website page for onboarding",
        "business_justification": "Sales demo this week",
        "acceptance_criteria": ["Landing page is viewable"],
    }
    updated = attach_optimized_prompt(spec)
    assert updated["ui_feature"] is True
    assert isinstance(updated["ui_keywords"], list)
    assert "optimized_prompt" in updated
    assert "UI delivery requirements" in updated["optimized_prompt"]

