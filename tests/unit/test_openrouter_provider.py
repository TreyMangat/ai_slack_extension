from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings, get_settings
from app.services.openrouter_provider import (
    OPENROUTER_API_URL,
    ModelTier,
    OpenRouterError,
    OpenRouterResponse,
    _BudgetTracker,
    _build_headers,
    _build_payload,
    _resolve_model,
    call_openrouter,
    call_openrouter_sync,
    estimate_cost,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    defaults = {
        "DATABASE_URL": "postgresql+psycopg2://x:x@db:5432/x",
        "REDIS_URL": "redis://redis:6379/0",
        "SECRET_KEY": "test",
        "OPENROUTER_API_KEY": "sk-or-test-key",
        "OPENROUTER_MINI_MODEL": "qwen/qwen3.5-9b",
        "OPENROUTER_FRONTIER_MODEL": "anthropic/claude-opus-4-6",
        "OPENROUTER_BUDGET_LIMIT_USD": "5.0",
        "OPENROUTER_REFERER": "https://example.com",
        "OPENROUTER_APP_TITLE": "TestApp",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


_GOOD_API_RESPONSE = {
    "choices": [{"message": {"content": "hello world"}}],
    "usage": {"prompt_tokens": 50, "completion_tokens": 20},
    "model": "qwen/qwen3.5-9b",
}


# ---------------------------------------------------------------------------
# Request construction
# ---------------------------------------------------------------------------


class TestRequestConstruction:
    def test_build_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        headers = _build_headers()
        assert headers["Authorization"] == "Bearer sk-or-test-key"
        assert headers["HTTP-Referer"] == "https://example.com"
        assert headers["X-Title"] == "TestApp"
        assert headers["Content-Type"] == "application/json"

    def test_build_payload_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        payload = _build_payload("hi", "qwen/qwen3.5-9b", ModelTier.MINI)
        assert payload["model"] == "qwen/qwen3.5-9b"
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        assert "response_format" not in payload

    def test_build_payload_json_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        payload = _build_payload("hi", "m", ModelTier.MINI, system_prompt="sys", response_format="json_object")
        assert payload["messages"][0] == {"role": "system", "content": "sys"}
        assert payload["messages"][1] == {"role": "user", "content": "hi"}
        assert payload["response_format"] == {"type": "json_object"}

    def test_build_payload_url_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the constant URL is correct."""
        assert OPENROUTER_API_URL == "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# Tier defaults and model override
# ---------------------------------------------------------------------------


class TestModelSelection:
    def test_mini_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        assert _resolve_model(ModelTier.MINI) == "qwen/qwen3.5-9b"

    def test_frontier_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        assert _resolve_model(ModelTier.FRONTIER) == "anthropic/claude-opus-4-6"

    def test_model_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        assert _resolve_model(ModelTier.MINI, model_override="custom/model") == "custom/model"

    def test_custom_tier_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch, OPENROUTER_MINI_MODEL="google/gemini-3-flash")
        assert _resolve_model(ModelTier.MINI) == "google/gemini-3-flash"


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


class TestCostEstimation:
    def test_known_model(self) -> None:
        cost = estimate_cost("qwen/qwen3.5-9b", input_tokens=1000, output_tokens=1000)
        assert cost == pytest.approx(0.0002 + 0.0006, abs=1e-8)

    def test_frontier_model(self) -> None:
        cost = estimate_cost("anthropic/claude-opus-4-6", input_tokens=1000, output_tokens=1000)
        assert cost == pytest.approx(0.015 + 0.075, abs=1e-8)

    def test_unknown_model_fallback(self) -> None:
        cost = estimate_cost("unknown/model", input_tokens=1000, output_tokens=1000)
        assert cost == pytest.approx(0.01 + 0.01, abs=1e-8)

    def test_zero_tokens(self) -> None:
        cost = estimate_cost("qwen/qwen3.5-9b", input_tokens=0, output_tokens=0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------


class TestBudgetTracker:
    def test_record_and_check(self) -> None:
        tracker = _BudgetTracker()
        tracker.record(2.0)
        assert tracker.spent_today == pytest.approx(2.0)
        tracker.check(5.0)  # should not raise

    def test_budget_exceeded(self) -> None:
        tracker = _BudgetTracker()
        tracker.record(6.0)
        with pytest.raises(OpenRouterError, match="budget exceeded"):
            tracker.check(5.0)

    def test_resets_on_new_day(self) -> None:
        tracker = _BudgetTracker()
        tracker.record(3.0)
        # Simulate a new day
        tracker._date = date(2000, 1, 1)
        assert tracker.spent_today == 0.0

    def test_reset_method(self) -> None:
        tracker = _BudgetTracker()
        tracker.record(4.0)
        tracker.reset()
        assert tracker.spent_today == 0.0


# ---------------------------------------------------------------------------
# Async call_openrouter
# ---------------------------------------------------------------------------


class TestCallOpenrouter:
    @pytest.mark.asyncio
    async def test_successful_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        from app.services import openrouter_provider
        openrouter_provider._budget.reset()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _GOOD_API_RESPONSE

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            result = await call_openrouter("test prompt", ModelTier.MINI)

        assert isinstance(result, OpenRouterResponse)
        assert result.content == "hello world"
        assert result.tier == ModelTier.MINI
        assert result.usage["input_tokens"] == 50

    @pytest.mark.asyncio
    async def test_non_200_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        from app.services import openrouter_provider
        openrouter_provider._budget.reset()

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "rate limited"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OpenRouterError, match="429"):
                await call_openrouter("test", ModelTier.MINI)

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch, OPENROUTER_API_KEY="")
        with pytest.raises(OpenRouterError, match="not configured"):
            await call_openrouter("test", ModelTier.MINI)

    @pytest.mark.asyncio
    async def test_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        from app.services import openrouter_provider
        openrouter_provider._budget.reset()

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ReadTimeout("timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.ReadTimeout):
                await call_openrouter("test", ModelTier.MINI)

    @pytest.mark.asyncio
    async def test_malformed_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        from app.services import openrouter_provider
        openrouter_provider._budget.reset()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"bad": "data"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(OpenRouterError, match="Unexpected"):
                await call_openrouter("test", ModelTier.MINI)

    @pytest.mark.asyncio
    async def test_model_override_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        from app.services import openrouter_provider
        openrouter_provider._budget.reset()

        response_data = dict(_GOOD_API_RESPONSE)
        response_data["model"] = "custom/override"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.openrouter_provider.httpx.AsyncClient", return_value=mock_client):
            result = await call_openrouter("test", ModelTier.MINI, model_override="custom/override")

        assert result.model == "custom/override"
        # Verify the request used the override model
        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["model"] == "custom/override"

    @pytest.mark.asyncio
    async def test_budget_exceeded_blocks_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch, OPENROUTER_BUDGET_LIMIT_USD="0.01")
        from app.services import openrouter_provider
        openrouter_provider._budget.reset()
        openrouter_provider._budget.record(0.02)

        with pytest.raises(OpenRouterError, match="budget exceeded"):
            await call_openrouter("test", ModelTier.MINI)


# ---------------------------------------------------------------------------
# Sync call_openrouter_sync
# ---------------------------------------------------------------------------


class TestCallOpenrouterSync:
    def test_successful_sync_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        from app.services import openrouter_provider
        openrouter_provider._budget.reset()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _GOOD_API_RESPONSE

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("app.services.openrouter_provider.httpx.Client", return_value=mock_client):
            result = call_openrouter_sync("test prompt", ModelTier.FRONTIER)

        assert isinstance(result, OpenRouterResponse)
        assert result.content == "hello world"
        assert result.tier == ModelTier.FRONTIER
