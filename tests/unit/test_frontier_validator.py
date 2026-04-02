"""Tests for frontier model spec validation."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas import FeatureRequestCreate
from app.services.feature_service import create_feature_request
from app.services.frontier_validator import (
    _SYSTEM_PROMPT,
    ValidationResult,
    validate_spec_with_frontier,
)
from app.services.openrouter_provider import ModelTier, OpenRouterResponse


def test_validation_result_defaults():
    r = ValidationResult()
    assert r.is_valid is False
    assert r.confidence == 0.0
    assert r.acceptance_criteria == []


@pytest.mark.asyncio
async def test_validate_clear_spec_returns_valid():
    """A clear spec should be marked valid by the frontier model."""
    mock_response = OpenRouterResponse(
        content=json.dumps(
            {
                "is_valid": True,
                "confidence": 0.95,
                "improved_title": "",
                "improved_problem": "",
                "acceptance_criteria": ["Toggle exists in settings", "Preference persists"],
                "missing_info": [],
                "suggestions": "",
                "reasoning": "Clear request with specific repo",
            }
        ),
        model="anthropic/claude-opus-4-6",
        usage={"input_tokens": 120, "output_tokens": 60},
        cost_estimate=0.0125,
        tier=ModelTier.FRONTIER,
    )

    with patch(
        "app.services.frontier_validator.call_openrouter",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await validate_spec_with_frontier(
            {
                "title": "Add dark mode",
                "problem": "Add a dark mode toggle to the settings page",
                "repo": "org/app",
            }
        )

    assert result.is_valid is True
    assert result.confidence >= 0.9
    assert result.tier == "frontier"
    assert result.model == "anthropic/claude-opus-4-6"


@pytest.mark.asyncio
async def test_validate_vague_spec_returns_invalid():
    """A vague spec should be marked invalid."""
    mock_response = OpenRouterResponse(
        content=json.dumps(
            {
                "is_valid": False,
                "confidence": 0.8,
                "improved_title": "",
                "improved_problem": "",
                "acceptance_criteria": [],
                "missing_info": ["What specifically should be improved?"],
                "suggestions": "Please describe what 'better' means",
                "reasoning": "Too vague to implement",
            }
        ),
        model="anthropic/claude-opus-4-6",
        usage={"input_tokens": 100, "output_tokens": 40},
        cost_estimate=0.0101,
        tier=ModelTier.FRONTIER,
    )

    with patch(
        "app.services.frontier_validator.call_openrouter",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await validate_spec_with_frontier(
            {
                "title": "Make it better",
                "problem": "Make it better",
                "repo": "org/app",
            }
        )

    assert result.is_valid is False
    assert len(result.missing_info) > 0


@pytest.mark.asyncio
async def test_validate_handles_api_failure_gracefully():
    """API failure should return invalid result, not raise."""
    with patch(
        "app.services.frontier_validator.call_openrouter",
        new_callable=AsyncMock,
        side_effect=ConnectionError("API down"),
    ):
        result = await validate_spec_with_frontier(
            {
                "title": "Test",
                "problem": "Test problem",
                "repo": "org/app",
            }
        )

    assert result.is_valid is False
    assert "error" in result.reasoning.lower()


def test_system_prompt_mentions_json():
    """System prompt should instruct JSON response format."""
    assert "JSON" in _SYSTEM_PROMPT


def test_validation_result_preserves_improvements():
    r = ValidationResult(
        is_valid=True,
        improved_title="Dark mode toggle",
        acceptance_criteria=["Toggle works", "Persists setting"],
    )
    assert r.improved_title == "Dark mode toggle"
    assert len(r.acceptance_criteria) == 2


def test_feature_service_applies_frontier_improvements(mock_db_session):
    """feature_service should apply title/problem/AC improvements from frontier."""
    payload = FeatureRequestCreate(
        spec={
            "title": "I want dark mode for my slack bot",
            "problem": "Add dark mode to the local Slack bot settings page",
            "repo": "org/app",
        },
        requester_user_id="U_TEST",
    )
    frontier_result = ValidationResult(
        is_valid=True,
        confidence=0.96,
        improved_title="Add settings dark mode",
        improved_problem="Add a dark mode toggle to the local Slack bot settings page.",
        acceptance_criteria=["Toggle appears in settings", "Theme choice persists locally"],
        reasoning="Clear feature with specific repo and outcome",
        model="anthropic/claude-opus-4-6",
        tier="frontier",
        usage={"input_tokens": 33, "output_tokens": 21},
        cost_estimate_usd=0.0042,
    )

    with (
        patch("app.services.feature_service.validate_spec", return_value=(True, [], [])),
        patch("app.services.feature_service.validate_spec_with_frontier_sync", return_value=frontier_result),
        patch(
            "app.services.feature_service.get_settings",
            return_value=MagicMock(openrouter_api_key="sk-or-test-key"),
        ),
    ):
        feature = create_feature_request(mock_db_session, payload)

    assert feature.title == "Add settings dark mode"
    assert feature.spec["problem"] == "Add a dark mode toggle to the local Slack bot settings page."
    assert feature.spec["acceptance_criteria"] == ["Toggle appears in settings", "Theme choice persists locally"]
    assert feature.spec["_frontier_validation"]["confidence"] == 0.96
    assert feature.llm_spec_analysis is not None
    assert feature.llm_spec_analysis["model"] == "anthropic/claude-opus-4-6"
    assert feature.llm_spec_analysis["tier"] == "frontier"
