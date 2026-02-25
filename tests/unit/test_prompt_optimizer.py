from __future__ import annotations

from app.services.prompt_optimizer import attach_optimized_prompt, build_optimized_prompt


def test_build_optimized_prompt_contains_core_sections() -> None:
    spec = {
        "title": "Invoice export",
        "problem": "Finance cannot export filtered invoice data.",
        "business_justification": "Needed for month-end close.",
        "implementation_mode": "new_feature",
        "repo": "acme/billing",
        "acceptance_criteria": ["User can export CSV", "Export respects filters"],
        "links": ["https://jira.example.com/ABC-123"],
    }

    prompt = build_optimized_prompt(spec)
    assert "Build Request" in prompt
    assert "Invoice export" in prompt
    assert "Acceptance criteria" in prompt
    assert "https://jira.example.com/ABC-123" in prompt


def test_attach_optimized_prompt_populates_spec_key() -> None:
    spec = {
        "title": "Dark mode",
        "problem": "Low contrast during night shifts.",
        "business_justification": "Support and retention for power users.",
        "acceptance_criteria": ["Theme toggle is persisted"],
    }

    updated = attach_optimized_prompt(spec)
    assert "optimized_prompt" in updated
    assert "Dark mode" in updated["optimized_prompt"]
    assert "UI delivery requirements" not in updated["optimized_prompt"]
    assert "ui_feature" not in updated
    assert "ui_keywords" not in updated
