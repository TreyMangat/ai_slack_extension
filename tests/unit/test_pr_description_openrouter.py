from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import get_settings
from app.services.openrouter_provider import ModelTier, OpenRouterError, OpenRouterResponse
from app.services.pr_description import build_pr_body_with_llm, build_standard_pr_body


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


_COMMON_KWARGS = {
    "spec": {"title": "Add search", "problem": "No search"},
    "feature_id": "feat-1",
    "issue_number": 10,
    "branch_name": "feat/search",
    "runner_name": "opencode",
    "runner_model": "gpt-5.4",
    "summary": "Added search bar",
    "verification_output": "tests pass",
    "verification_command": "pytest -q",
    "verification_warning": "",
    "preview_url": "https://preview.example.com",
    "cloudflare_project_name": "prfactory",
    "cloudflare_production_branch": "main",
}


# ---------------------------------------------------------------------------
# Frontier call is made when key configured
# ---------------------------------------------------------------------------


class TestFrontierCallMade:
    @pytest.mark.asyncio
    async def test_llm_body_returned_when_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        mock_response = OpenRouterResponse(
            content="## Why\nUser needs search\n\n## What Changed\n- Added search bar",
            model="anthropic/claude-opus-4-6",
            usage={"input_tokens": 300, "output_tokens": 200},
            cost_estimate=0.02,
            tier=ModelTier.FRONTIER,
        )
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_call:
            result = await build_pr_body_with_llm(**_COMMON_KWARGS)

        mock_call.assert_called_once()
        assert "User needs search" in result
        assert "## Why" in result


# ---------------------------------------------------------------------------
# Fallback when key not set
# ---------------------------------------------------------------------------


class TestFallbackNoKey:
    @pytest.mark.asyncio
    async def test_no_key_uses_template(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch, OPENROUTER_API_KEY="")
        result = await build_pr_body_with_llm(**_COMMON_KWARGS)
        # Template body should contain standard sections
        assert "## Why" in result
        assert "Add search" in result


# ---------------------------------------------------------------------------
# Fallback on error
# ---------------------------------------------------------------------------


class TestFallbackOnError:
    @pytest.mark.asyncio
    async def test_provider_error_falls_back_to_template(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=OpenRouterError(500, "server error"),
        ):
            result = await build_pr_body_with_llm(**_COMMON_KWARGS)

        # Falls back to template
        assert "## Why" in result
        assert "Add search" in result

    @pytest.mark.asyncio
    async def test_timeout_falls_back_to_template(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)
        import httpx

        with patch(
            "app.services.openrouter_provider.call_openrouter",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("timeout"),
        ):
            result = await build_pr_body_with_llm(**_COMMON_KWARGS)

        assert "## Why" in result
