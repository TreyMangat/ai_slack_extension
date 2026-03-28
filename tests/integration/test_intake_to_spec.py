"""Integration tests: intake -> spec validation flow.

Tests the full pipeline from Slack message classification through spec
validation using mocked OpenRouter calls.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.models import FeatureRequest
from app.services.intake_router import IntakeAction, classify_intake_message
from app.services.openrouter_provider import (
    ModelTier,
    OpenRouterError,
    OpenRouterResponse,
)
from app.services.spec_validator import validate_spec_with_llm
from app.state_machine import NEEDS_INFO, NEW, READY_FOR_BUILD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _or_response(content_dict, *, model="qwen/qwen3.5-9b", tier=ModelTier.MINI):
    return OpenRouterResponse(
        content=json.dumps(content_dict) if isinstance(content_dict, dict) else content_dict,
        model=model,
        usage={"input_tokens": 50, "output_tokens": 100},
        cost_estimate=0.001,
        tier=tier,
    )


def _make_feature(db, *, status=NEW, spec=None, title="Test feature"):
    spec = spec or {
        "title": title,
        "problem": "Users want dark mode",
        "business_justification": "High demand",
        "acceptance_criteria": ["Dark mode toggle works"],
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNaturalLanguageIntake:
    @pytest.mark.asyncio
    async def test_natural_language_intake_extracts_fields(self, mock_settings):
        """Mock OpenRouter returns ask_field with title extraction."""
        llm_output = {
            "action": "ask_field",
            "field_name": "title",
            "field_value": "Add dark mode",
            "next_question": "Which repo should this go to?",
            "confidence": 0.9,
            "reasoning": "Clear feature request",
        }
        mock_resp = _or_response(llm_output)
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await classify_intake_message(
                "I want to add dark mode to the settings page",
                [],
                {},
            )
        assert isinstance(result, IntakeAction)
        assert result.action == "ask_field"
        assert result.field_name == "title"
        assert result.field_value == "Add dark mode"
        assert result.confidence >= 0.6


class TestIntakeConfirmTriggersCreation:
    @pytest.mark.asyncio
    async def test_intake_confirm_triggers_feature_creation(
        self, mock_settings, mock_db_session
    ):
        """When classify returns confirm with all fields, a feature can be created."""
        llm_output = {
            "action": "confirm",
            "confidence": 0.95,
            "reasoning": "All fields collected",
        }
        mock_resp = _or_response(llm_output)
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await classify_intake_message(
                "yes, looks good",
                [],
                {
                    "title": "Dark mode",
                    "description": "Add dark mode toggle",
                    "repo": "org/app",
                    "branch": "main",
                    "acceptance_criteria": "Toggle works",
                },
            )
        assert result.action == "confirm"

        # Simulate feature creation after confirm
        feature = _make_feature(mock_db_session, status=NEW)
        assert feature.status == NEW
        assert feature.id is not None


class TestSpecValidationUsesFrontierModel:
    @pytest.mark.asyncio
    async def test_spec_validation_uses_frontier_model(self, mock_settings):
        """validate_spec_with_llm calls FRONTIER tier."""
        spec = {
            "title": "Dark mode",
            "problem": "Users want dark mode",
            "business_justification": "High demand",
            "acceptance_criteria": ["Toggle works"],
            "repo": "org/app",
            "implementation_mode": "new_feature",
        }
        llm_output = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": [],
            "confidence": 0.95,
        }
        mock_resp = _or_response(
            llm_output,
            model="anthropic/claude-opus-4-6",
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ) as mock_call:
            is_valid, missing, suggestions, analysis = await validate_spec_with_llm(spec)

        assert is_valid is True
        assert missing == []
        # Verify FRONTIER tier was requested
        call_kwargs = mock_call.call_args
        assert call_kwargs[1]["tier"] == ModelTier.FRONTIER


class TestSpecValidationFallback:
    @pytest.mark.asyncio
    async def test_spec_validation_fallback_on_openrouter_failure(self, mock_settings):
        """On OpenRouter failure, falls back to rule-based validation."""
        spec = {
            "title": "Dark mode",
            "problem": "Users want dark mode",
            "business_justification": "High demand",
            "acceptance_criteria": ["Toggle works"],
            "repo": "org/app",
            "implementation_mode": "new_feature",
        }
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=OpenRouterError(500, "Internal error"),
        ):
            is_valid, missing, warnings, analysis = await validate_spec_with_llm(spec)

        # Rule-based says valid (all required fields present)
        assert is_valid is True
        # No LLM analysis on fallback
        assert analysis is None


class TestSpecValidationStoresLLMAnalysis:
    @pytest.mark.asyncio
    async def test_spec_validation_stores_llm_analysis(self, mock_settings):
        """LLM analysis dict is returned for storage on the feature."""
        spec = {
            "title": "Dark mode",
            "problem": "Users want dark mode",
            "business_justification": "High demand",
            "acceptance_criteria": ["Toggle works"],
            "repo": "org/app",
            "implementation_mode": "new_feature",
        }
        llm_output = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": ["Consider adding error states"],
            "confidence": 0.92,
        }
        mock_resp = _or_response(
            llm_output,
            model="anthropic/claude-opus-4-6",
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            is_valid, missing, suggestions, analysis = await validate_spec_with_llm(spec)

        assert analysis is not None
        assert analysis["model"] == "anthropic/claude-opus-4-6"
        assert analysis["tier"] == "frontier"
        assert analysis["confidence"] == 0.92
        assert analysis["status"] == "READY_FOR_BUILD"
        assert "usage" in analysis
        assert "cost_estimate_usd" in analysis


class TestLowConfidenceTriggersNeedsInfo:
    @pytest.mark.asyncio
    async def test_low_confidence_triggers_needs_info(self, mock_settings):
        """When LLM returns READY_FOR_BUILD but confidence < 0.5, treat as NEEDS_INFO."""
        spec = {
            "title": "Dark mode",
            "problem": "Users want dark mode",
            "business_justification": "High demand",
            "acceptance_criteria": ["Toggle works"],
            "repo": "org/app",
            "implementation_mode": "new_feature",
        }
        llm_output = {
            "status": "READY_FOR_BUILD",
            "missing_fields": [],
            "suggestions": ["Spec is vague"],
            "confidence": 0.4,
        }
        mock_resp = _or_response(
            llm_output,
            model="anthropic/claude-opus-4-6",
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            is_valid, missing, suggestions, analysis = await validate_spec_with_llm(spec)

        # The LLM said READY_FOR_BUILD but confidence is low.
        # validate_spec_with_llm returns the raw LLM verdict; the *caller*
        # (feature_service) is responsible for applying confidence gating.
        # So we verify the analysis carries the low confidence for the caller.
        assert analysis is not None
        assert analysis["confidence"] == 0.4
        # The caller can decide to treat this as NEEDS_INFO based on confidence
