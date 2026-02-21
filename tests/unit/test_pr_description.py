from __future__ import annotations

from app.services.pr_description import build_standard_pr_body


def test_build_standard_pr_body_for_ui_without_preview_url() -> None:
    spec = {
        "title": "Add checkout page UI",
        "problem": "Need a new checkout interface with cleaner layout",
        "business_justification": "Improve conversion in checkout flow",
        "acceptance_criteria": ["Checkout form renders", "Primary CTA is visible"],
        "ui_feature": True,
    }
    body = build_standard_pr_body(
        spec=spec,
        feature_id="11111111-1111-1111-1111-111111111111",
        issue_number=12,
        branch_name="feature-factory/test",
        runner_name="opencode-local-openclaw",
        runner_model="openai-codex/gpt-5.3-codex",
        summary="Implemented checkout layout and call-to-action refinements.",
        verification_output="npm run build passed",
        verification_command="pytest -q",
        verification_warning="",
        preview_url="",
        cloudflare_project_name="ff-pages",
        cloudflare_production_branch="main",
    )
    assert "## UI Preview" in body
    assert "Cloudflare Pages" in body
    assert "## What Changed" in body
    assert "## Acceptance Criteria" in body


def test_build_standard_pr_body_for_non_ui_includes_example_output() -> None:
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
    assert "## UI Preview" not in body

