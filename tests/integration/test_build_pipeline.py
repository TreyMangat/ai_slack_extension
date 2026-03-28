"""Integration tests: build -> PR -> notification flow.

Tests state machine transitions, PR description generation (LLM vs template),
and cost tracking through the build pipeline.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import FeatureEvent, FeatureRequest
from app.services.cost_tracker import get_feature_cost_summary, record_cost
from app.services.event_logger import log_event
from app.services.openrouter_provider import (
    ModelTier,
    OpenRouterError,
    OpenRouterResponse,
)
from app.services.pr_description import build_pr_body_with_llm, build_standard_pr_body
from app.state_machine import (
    BUILDING,
    FAILED_BUILD,
    MERGED,
    NEEDS_HUMAN,
    NEEDS_INFO,
    NEW,
    PR_OPENED,
    PREVIEW_READY,
    PRODUCT_APPROVED,
    READY_FOR_BUILD,
    READY_TO_MERGE,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _or_response(content, *, model="anthropic/claude-opus-4-6", tier=ModelTier.FRONTIER):
    return OpenRouterResponse(
        content=content if isinstance(content, str) else json.dumps(content),
        model=model,
        usage={"input_tokens": 100, "output_tokens": 200},
        cost_estimate=0.01,
        tier=tier,
    )


def _make_feature(db, *, status=READY_FOR_BUILD, title="Test feature", spec=None):
    spec = spec or {
        "title": title,
        "problem": "Users want dark mode",
        "business_justification": "High demand",
        "acceptance_criteria": ["Toggle works"],
        "repo": "org/app",
        "implementation_mode": "new_feature",
    }
    feature = FeatureRequest(
        status=status,
        title=title,
        requester_user_id="U123",
        spec=spec,
    )
    db.add(feature)
    db.flush()
    return feature


def _pr_kwargs(feature):
    return dict(
        spec=feature.spec,
        feature_id=feature.id,
        issue_number=None,
        branch_name="feat/dark-mode",
        runner_name="opencode",
        runner_model="gpt-5.3-codex",
        summary="Added dark mode toggle",
        verification_output="All tests pass",
        verification_command="pytest -q",
        verification_warning="",
        preview_url="",
        cloudflare_project_name="",
        cloudflare_production_branch="main",
    )


# ---------------------------------------------------------------------------
# PR description tests
# ---------------------------------------------------------------------------


class TestBuildJobUsesLLMPRDescription:
    @pytest.mark.asyncio
    async def test_build_job_uses_llm_pr_description(self, mock_settings, mock_db_session):
        """When OpenRouter succeeds, PR body comes from LLM."""
        feature = _make_feature(mock_db_session)
        llm_body = "## Why\nUser requested dark mode\n\n## What Changed\n- Added toggle"
        mock_resp = _or_response(llm_body)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await build_pr_body_with_llm(**_pr_kwargs(feature))

        assert "dark mode" in result.lower()
        assert result == llm_body


class TestBuildJobFallsBackToTemplate:
    @pytest.mark.asyncio
    async def test_build_job_falls_back_to_template_pr(self, mock_settings, mock_db_session):
        """When OpenRouter fails, PR body comes from template."""
        feature = _make_feature(mock_db_session)

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=OpenRouterError(500, "Internal error"),
        ):
            result = await build_pr_body_with_llm(**_pr_kwargs(feature))

        # Template includes structured sections
        assert "## Why" in result
        assert "## What Changed" in result
        assert "## Acceptance Criteria" in result
        assert feature.id in result


class TestBuildRecordsLLMCosts:
    def test_build_records_llm_costs(self, mock_settings, mock_db_session):
        """Cost events are recorded for spec validation and PR description."""
        feature = _make_feature(mock_db_session)

        # Record spec validation cost
        record_cost(
            mock_db_session,
            feature,
            tier="frontier",
            model="anthropic/claude-opus-4-6",
            tokens_in=200,
            tokens_out=150,
            cost_usd=0.0143,
            operation="spec_validation",
        )
        mock_db_session.flush()

        # Record PR description cost
        record_cost(
            mock_db_session,
            feature,
            tier="frontier",
            model="anthropic/claude-opus-4-6",
            tokens_in=300,
            tokens_out=400,
            cost_usd=0.0345,
            operation="pr_description",
        )
        mock_db_session.flush()

        # Query cost events
        cost_events = [
            e for e in feature.events if e.event_type == "llm_cost"
        ]
        assert len(cost_events) >= 2

        for ev in cost_events:
            assert ev.data["model"] == "anthropic/claude-opus-4-6"
            assert ev.data["tier"] == "frontier"
            assert "tokens_in" in ev.data
            assert "tokens_out" in ev.data
            assert "cost_usd" in ev.data


class TestCostSummaryAggregation:
    def test_cost_summary_aggregation(self, mock_settings, mock_db_session):
        """get_feature_cost_summary correctly aggregates cost events."""
        feature = _make_feature(mock_db_session)

        # 2 frontier calls
        record_cost(
            mock_db_session, feature,
            tier="frontier", model="anthropic/claude-opus-4-6",
            tokens_in=100, tokens_out=200, cost_usd=0.01, operation="spec_validation",
        )
        record_cost(
            mock_db_session, feature,
            tier="frontier", model="anthropic/claude-opus-4-6",
            tokens_in=300, tokens_out=400, cost_usd=0.03, operation="pr_description",
        )
        # 1 mini call
        record_cost(
            mock_db_session, feature,
            tier="mini", model="qwen/qwen3.5-9b",
            tokens_in=50, tokens_out=20, cost_usd=0.0001, operation="intake_classify",
        )
        mock_db_session.flush()

        summary = get_feature_cost_summary(mock_db_session, feature.id)
        assert summary["calls"] == 3
        assert summary["total_usd"] == pytest.approx(0.0401, abs=1e-6)
        assert summary["by_tier"]["frontier"] == pytest.approx(0.04, abs=1e-6)
        assert summary["by_tier"]["mini"] == pytest.approx(0.0001, abs=1e-6)


# ---------------------------------------------------------------------------
# State machine transition tests
# ---------------------------------------------------------------------------


class TestStateTransitionsHappyPath:
    def test_state_transitions_through_happy_path(self):
        """Full happy path: NEW -> ... -> MERGED."""
        validate_transition(NEW, READY_FOR_BUILD)
        validate_transition(READY_FOR_BUILD, BUILDING)
        validate_transition(BUILDING, PR_OPENED)
        validate_transition(PR_OPENED, PREVIEW_READY)
        validate_transition(PREVIEW_READY, PRODUCT_APPROVED)
        validate_transition(PRODUCT_APPROVED, READY_TO_MERGE)
        validate_transition(READY_TO_MERGE, MERGED)

        # MERGED is terminal
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(MERGED, NEW)
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(MERGED, BUILDING)


class TestStateTransitionsFailurePaths:
    def test_building_to_failed_build(self):
        validate_transition(BUILDING, FAILED_BUILD)

    def test_failed_build_to_ready_for_build_retry(self):
        validate_transition(FAILED_BUILD, READY_FOR_BUILD)

    def test_failed_build_to_needs_human(self):
        validate_transition(FAILED_BUILD, NEEDS_HUMAN)

    def test_cannot_skip_new_to_merged(self):
        with pytest.raises(ValueError):
            validate_transition(NEW, MERGED)

    def test_cannot_go_backward_pr_opened_to_new(self):
        with pytest.raises(ValueError):
            validate_transition(PR_OPENED, NEW)

    def test_cannot_skip_new_to_building(self):
        with pytest.raises(ValueError):
            validate_transition(NEW, BUILDING)

    def test_cannot_skip_new_to_pr_opened(self):
        with pytest.raises(ValueError):
            validate_transition(NEW, PR_OPENED)

    def test_needs_human_to_ready_for_build(self):
        validate_transition(NEEDS_HUMAN, READY_FOR_BUILD)

    def test_needs_human_to_needs_info(self):
        validate_transition(NEEDS_HUMAN, NEEDS_INFO)
