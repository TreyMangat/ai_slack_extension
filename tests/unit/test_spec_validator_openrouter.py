from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.config import get_settings
from app.services.openrouter_provider import ModelTier, OpenRouterError, OpenRouterResponse
from app.services.spec_validator import validate_spec, validate_spec_with_llm


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
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


_COMPLETE_SPEC = {
    "title": "Add dark mode",
    "problem": "Users complain about eye strain",
    "business_justification": "Retention improvement",
    "acceptance_criteria": ["Toggle in settings", "Persists across sessions"],
    "repo": "org/frontend",
    "implementation_mode": "new_feature",
}

_INCOMPLETE_SPEC = {
    "title": "Something",
    "problem": "",
    "repo": "org/frontend",
    "business_justification": "",
    "acceptance_criteria": [],
}


# ---------------------------------------------------------------------------
# Frontier call is made when key is configured
# ---------------------------------------------------------------------------


class TestFrontierCallMade:
    @pytest.mark.asyncio
    async def test_llm_called_when_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = OpenRouterResponse(
            content=json.dumps({
                "status": "READY_FOR_BUILD",
                "missing_fields": [],
                "suggestions": ["Consider adding error scenarios to acceptance criteria"],
                "confidence": 0.92,
            }),
            model="anthropic/claude-opus-4-6",
            usage={"input_tokens": 200, "output_tokens": 100},
            cost_estimate=0.0105,
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_call:
            is_valid, missing, suggestions, llm_analysis = await validate_spec_with_llm(_COMPLETE_SPEC)

        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args
        assert call_kwargs[1]["tier"] == ModelTier.FRONTIER
        assert call_kwargs[1]["response_format"] == "json_object"
        assert is_valid is True
        assert missing == []
        assert "error scenarios" in suggestions[0]
        assert llm_analysis is not None
        assert llm_analysis["model"] == "anthropic/claude-opus-4-6"
        assert llm_analysis["confidence"] == 0.92

    @pytest.mark.asyncio
    async def test_llm_returns_needs_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = OpenRouterResponse(
            content=json.dumps({
                "status": "NEEDS_INFO",
                "missing_fields": ["problem", "business_justification", "acceptance_criteria"],
                "suggestions": ["Add a clear problem statement"],
                "confidence": 0.88,
            }),
            model="anthropic/claude-opus-4-6",
            usage={"input_tokens": 150, "output_tokens": 80},
            cost_estimate=0.008,
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            is_valid, missing, suggestions, llm_analysis = await validate_spec_with_llm(_INCOMPLETE_SPEC)

        assert is_valid is False
        assert "problem" in missing
        assert len(suggestions) > 0
        assert llm_analysis is not None
        assert llm_analysis["status"] == "NEEDS_INFO"


# ---------------------------------------------------------------------------
# Fallback when key is empty
# ---------------------------------------------------------------------------


class TestFallbackNoKey:
    @pytest.mark.asyncio
    async def test_no_key_uses_rule_based(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch, OPENROUTER_API_KEY="")
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
        ) as mock_call:
            is_valid, missing, warnings, llm_analysis = await validate_spec_with_llm(_COMPLETE_SPEC)

        mock_call.assert_not_called()
        assert is_valid is True
        assert llm_analysis is None

    @pytest.mark.asyncio
    async def test_no_key_incomplete_spec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch, OPENROUTER_API_KEY="")
        is_valid, missing, warnings, llm_analysis = await validate_spec_with_llm(_INCOMPLETE_SPEC)
        assert is_valid is False
        assert "problem" in missing
        assert llm_analysis is None


# ---------------------------------------------------------------------------
# Fallback on OpenRouterError
# ---------------------------------------------------------------------------


class TestFallbackOnError:
    @pytest.mark.asyncio
    async def test_provider_error_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=OpenRouterError(500, "server error"),
        ):
            is_valid, missing, warnings, llm_analysis = await validate_spec_with_llm(_COMPLETE_SPEC)

        assert is_valid is True
        assert llm_analysis is None

    @pytest.mark.asyncio
    async def test_json_parse_error_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        bad_response = OpenRouterResponse(
            content="not json",
            model="anthropic/claude-opus-4-6",
            usage={"input_tokens": 100, "output_tokens": 50},
            cost_estimate=0.005,
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=bad_response,
        ):
            is_valid, missing, warnings, llm_analysis = await validate_spec_with_llm(_COMPLETE_SPEC)

        assert is_valid is True
        assert llm_analysis is None

    @pytest.mark.asyncio
    async def test_timeout_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        import httpx

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("timeout"),
        ):
            is_valid, missing, warnings, llm_analysis = await validate_spec_with_llm(_COMPLETE_SPEC)

        assert is_valid is True
        assert llm_analysis is None


# ---------------------------------------------------------------------------
# Rule-based validate_spec still works unchanged
# ---------------------------------------------------------------------------


class TestRuleBasedUnchanged:
    def test_complete_spec_valid(self) -> None:
        is_valid, missing, warnings = validate_spec(_COMPLETE_SPEC)
        assert is_valid is True
        assert missing == []

    def test_incomplete_spec_invalid(self) -> None:
        is_valid, missing, warnings = validate_spec(_INCOMPLETE_SPEC)
        assert is_valid is False
        assert "problem" in missing
        assert "business_justification" not in missing
        assert "acceptance_criteria" not in missing
