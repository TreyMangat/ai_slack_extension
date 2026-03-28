"""Integration tests: OpenRouter provider deeper E2E tests.

Tests tier routing, model override, JSON format, budget tracking,
timeouts, and cost estimation more deeply than unit tests.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import get_settings
from app.services.openrouter_provider import (
    TIER_TIMEOUTS,
    ModelTier,
    OpenRouterError,
    OpenRouterResponse,
    _budget,
    call_openrouter,
    estimate_cost,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_httpx_client(response_data, status_code=200):
    """Create a mock httpx.AsyncClient that returns the given response."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_data
    mock_response.text = json.dumps(response_data) if isinstance(response_data, dict) else str(response_data)

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


_GOOD_RESPONSE = {
    "choices": [{"message": {"content": "test response"}}],
    "model": "qwen/qwen3.5-9b",
    "usage": {"prompt_tokens": 50, "completion_tokens": 100},
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMiniTierUsesCorrectModel:
    @pytest.mark.asyncio
    async def test_mini_tier_uses_correct_model(self, mock_settings):
        """MINI tier sends the configured mini model in the request."""
        _budget.reset()
        mock_client = _mock_httpx_client(_GOOD_RESPONSE)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            result = await call_openrouter("test", ModelTier.MINI)

        call_args = mock_client.post.call_args
        request_body = call_args[1]["json"]
        assert request_body["model"] == "qwen/qwen3.5-9b"
        assert result.tier == ModelTier.MINI


class TestFrontierTierUsesCorrectModel:
    @pytest.mark.asyncio
    async def test_frontier_tier_uses_correct_model(self, mock_settings):
        """FRONTIER tier sends the configured frontier model in the request."""
        _budget.reset()
        frontier_response = dict(_GOOD_RESPONSE)
        frontier_response["model"] = "anthropic/claude-opus-4-6"
        mock_client = _mock_httpx_client(frontier_response)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            result = await call_openrouter("test", ModelTier.FRONTIER)

        call_args = mock_client.post.call_args
        request_body = call_args[1]["json"]
        assert request_body["model"] == "anthropic/claude-opus-4-6"
        assert result.tier == ModelTier.FRONTIER


class TestModelOverrideTakesPrecedence:
    @pytest.mark.asyncio
    async def test_model_override_takes_precedence(self, mock_settings):
        """model_override overrides the tier default."""
        _budget.reset()
        override_response = dict(_GOOD_RESPONSE)
        override_response["model"] = "google/gemini-3-flash"
        mock_client = _mock_httpx_client(override_response)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            result = await call_openrouter(
                "test",
                ModelTier.MINI,
                model_override="google/gemini-3-flash",
            )

        call_args = mock_client.post.call_args
        request_body = call_args[1]["json"]
        assert request_body["model"] == "google/gemini-3-flash"
        assert result.model == "google/gemini-3-flash"


class TestJSONResponseFormat:
    @pytest.mark.asyncio
    async def test_json_response_format(self, mock_settings):
        """response_format='json_object' includes format in request body."""
        _budget.reset()
        mock_client = _mock_httpx_client(_GOOD_RESPONSE)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            await call_openrouter(
                "test",
                ModelTier.MINI,
                response_format="json_object",
            )

        call_args = mock_client.post.call_args
        request_body = call_args[1]["json"]
        assert request_body["response_format"] == {"type": "json_object"}


class TestBudgetTrackingBlocksWhenExceeded:
    @pytest.mark.asyncio
    async def test_budget_tracking_blocks_when_exceeded(self, mock_settings, monkeypatch):
        """After spending exceeds budget, subsequent calls are blocked."""
        monkeypatch.setenv("OPENROUTER_BUDGET_LIMIT_USD", "0.001")
        get_settings.cache_clear()
        _budget.reset()

        # First call: succeeds and records cost that exceeds budget
        expensive_response = dict(_GOOD_RESPONSE)
        expensive_response["usage"] = {"prompt_tokens": 5000, "completion_tokens": 5000}
        mock_client = _mock_httpx_client(expensive_response)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            first_result = await call_openrouter("test", ModelTier.MINI)

        assert first_result.content == "test response"

        # Second call: should be blocked by budget
        with pytest.raises(OpenRouterError, match="budget"):
            await call_openrouter("test2", ModelTier.MINI)

        _budget.reset()


class TestTimeoutDiffersByTier:
    @pytest.mark.asyncio
    async def test_timeout_differs_by_tier(self, mock_settings):
        """MINI and FRONTIER tiers use different timeout values."""
        _budget.reset()
        mock_client = _mock_httpx_client(_GOOD_RESPONSE)

        # MINI call
        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await call_openrouter("test", ModelTier.MINI)

        mini_timeout = mock_cls.call_args[1].get("timeout", mock_cls.call_args[0][0] if mock_cls.call_args[0] else None)
        assert mini_timeout == TIER_TIMEOUTS[ModelTier.MINI]  # 30.0

        # FRONTIER call
        _budget.reset()
        frontier_response = dict(_GOOD_RESPONSE)
        frontier_response["model"] = "anthropic/claude-opus-4-6"
        mock_client2 = _mock_httpx_client(frontier_response)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client2) as mock_cls2:
            await call_openrouter("test", ModelTier.FRONTIER)

        frontier_timeout = mock_cls2.call_args[1].get("timeout", None)
        assert frontier_timeout == TIER_TIMEOUTS[ModelTier.FRONTIER]  # 120.0
        assert frontier_timeout > mini_timeout


class TestCostEstimationAccuracy:
    def test_known_model_cost(self):
        """Known model uses exact rates from COST_TABLE."""
        cost = estimate_cost("qwen/qwen3.5-9b", input_tokens=1000, output_tokens=1000)
        # qwen rates: input=0.0002/1K, output=0.0006/1K
        expected = (1000 / 1000) * 0.0002 + (1000 / 1000) * 0.0006
        assert cost == pytest.approx(expected, abs=1e-8)

    def test_frontier_model_cost(self):
        """Frontier model uses higher rates."""
        cost = estimate_cost("anthropic/claude-opus-4-6", input_tokens=1000, output_tokens=1000)
        # claude rates: input=0.015/1K, output=0.075/1K
        expected = (1000 / 1000) * 0.015 + (1000 / 1000) * 0.075
        assert cost == pytest.approx(expected, abs=1e-8)

    def test_unknown_model_fallback_rate(self):
        """Unknown model uses fallback rate of $0.01/1K for both."""
        cost = estimate_cost("unknown/model-xyz", input_tokens=1000, output_tokens=1000)
        expected = (1000 / 1000) * 0.01 + (1000 / 1000) * 0.01
        assert cost == pytest.approx(expected, abs=1e-8)

    def test_zero_tokens_zero_cost(self):
        cost = estimate_cost("qwen/qwen3.5-9b", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_asymmetric_token_counts(self):
        """Different input/output token counts produce correct asymmetric cost."""
        cost = estimate_cost("qwen/qwen3.5-9b", input_tokens=2000, output_tokens=500)
        expected = (2000 / 1000) * 0.0002 + (500 / 1000) * 0.0006
        assert cost == pytest.approx(expected, abs=1e-8)
