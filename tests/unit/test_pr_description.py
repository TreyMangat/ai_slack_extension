from __future__ import annotations

from app.services.pr_description import build_standard_pr_body


def test_build_standard_pr_body_includes_example_output_when_no_preview() -> None:
    spec = {
        "title": "Add API pagination",
        "problem": "Large responses are hard to consume",
        "business_justification": "Stability and performance",
        "acceptance_criteria": ["List endpoint supports limit/offset"],
    }
    body = build_standard_pr_body(
        spec=spec,
        feature_id="22222222-2222-2222-2222-222222222222",
        issue_number=44,
        branch_name="feature-factory/test2",
        runner_name="native-llm",
        runner_model="gpt-4.1-mini",
        summary="Added limit/offset support to list endpoint.",
        verification_output="GET /api/items?limit=20&offset=0 returned 200",
        verification_command="pytest -q",
        verification_warning="",
        preview_url="",
        cloudflare_project_name="",
        cloudflare_production_branch="main",
    )
    assert "## Example Output / Logs" in body
    assert "GET /api/items?limit=20&offset=0 returned 200" in body
    assert "## Preview" not in body


def test_build_standard_pr_body_includes_preview_when_available() -> None:
    spec = {
        "title": "Add export command",
        "problem": "Users need downloadable reports",
        "business_justification": "Support operations workflows",
        "acceptance_criteria": ["Command writes CSV report"],
    }
    body = build_standard_pr_body(
        spec=spec,
        feature_id="33333333-3333-3333-3333-333333333333",
        issue_number=55,
        branch_name="feature-factory/test3",
        runner_name="opencode-local-openclaw",
        runner_model="openai-codex/gpt-5.3-codex",
        summary="Added export command and file writer.",
        verification_output="pytest -q passed",
        verification_command="pytest -q",
        verification_warning="",
        preview_url="https://preview.example.com/run/123",
        cloudflare_project_name="ignored-now",
        cloudflare_production_branch="main",
    )
    assert "## Preview" in body
    assert "https://preview.example.com/run/123" in body
    assert "## Example Output / Logs" not in body
