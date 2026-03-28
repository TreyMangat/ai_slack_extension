"""OpenRouter unified LLM provider with two-tier model routing.

MINI tier  — fast/cheap models for Slack intake parsing, field extraction.
FRONTIER tier — powerful models for spec validation, PR descriptions, code review.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    MINI = "mini"
    FRONTIER = "frontier"


class OpenRouterResponse(BaseModel):
    content: str
    model: str
    usage: dict  # {"input_tokens": int, "output_tokens": int}
    cost_estimate: float
    tier: ModelTier


class OpenRouterError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

# Approximate USD per 1 000 tokens (input, output) for common models.
# Used only for budget-tracking — not billing. Fallback for unknown models.
COST_TABLE: dict[str, tuple[float, float]] = {
    "qwen/qwen3.5-9b": (0.0002, 0.0006),
    "google/gemini-3-flash": (0.0001, 0.0004),
    "openai/gpt-5.4-mini": (0.0004, 0.0016),
    "anthropic/claude-opus-4-6": (0.015, 0.075),
    "openai/gpt-5.4": (0.005, 0.015),
    "google/gemini-3.1-pro": (0.00125, 0.005),
    "qwen/qwen3.5-vl": (0.0012, 0.005),
}

_FALLBACK_COST_PER_1K = 0.01


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = COST_TABLE.get(model, (_FALLBACK_COST_PER_1K, _FALLBACK_COST_PER_1K))
    return (input_tokens / 1000) * rates[0] + (output_tokens / 1000) * rates[1]


# ---------------------------------------------------------------------------
# Daily budget tracker (in-memory, resets each UTC date)
# ---------------------------------------------------------------------------

class _BudgetTracker:
    def __init__(self) -> None:
        self._date: date | None = None
        self._spent: float = 0.0

    def record(self, cost: float) -> None:
        today = date.today()
        if self._date != today:
            self._date = today
            self._spent = 0.0
        self._spent += cost

    def check(self, limit: float) -> None:
        today = date.today()
        if self._date != today:
            return
        if self._spent >= limit:
            raise OpenRouterError(
                status_code=429,
                message=f"Daily OpenRouter budget exceeded: ${self._spent:.4f} >= ${limit:.2f}",
            )

    @property
    def spent_today(self) -> float:
        if self._date != date.today():
            return 0.0
        return self._spent

    def reset(self) -> None:
        self._date = None
        self._spent = 0.0


_budget = _BudgetTracker()


# ---------------------------------------------------------------------------
# Tier timeouts
# ---------------------------------------------------------------------------

TIER_TIMEOUTS: dict[ModelTier, float] = {
    ModelTier.MINI: 30.0,
    ModelTier.FRONTIER: 120.0,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_model(tier: ModelTier, model_override: str | None = None) -> str:
    if model_override:
        return model_override
    settings = get_settings()
    if tier == ModelTier.MINI:
        return settings.openrouter_mini_model or "qwen/qwen3.5-9b"
    return settings.openrouter_frontier_model or "anthropic/claude-opus-4-6"


def _build_headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_app_title,
    }


def _build_payload(
    prompt: str,
    model: str,
    tier: ModelTier,
    system_prompt: str | None = None,
    response_format: str = "text",
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
    }
    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def _parse_response(
    data: dict[str, Any],
    tier: ModelTier,
    model: str,
    latency_ms: float,
) -> OpenRouterResponse:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(
            status_code=0,
            message=f"Unexpected OpenRouter response format: {exc}",
        ) from exc

    usage_raw = data.get("usage") or {}
    input_tokens = int(usage_raw.get("prompt_tokens", 0))
    output_tokens = int(usage_raw.get("completion_tokens", 0))
    actual_model = data.get("model", model)

    cost = estimate_cost(actual_model, input_tokens, output_tokens)
    _budget.record(cost)

    logger.info(
        "openrouter_call",
        extra={
            "tier": tier.value,
            "model": actual_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_estimate_usd": round(cost, 6),
            "latency_ms": round(latency_ms, 1),
        },
    )

    return OpenRouterResponse(
        content=content,
        model=actual_model,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
        cost_estimate=cost,
        tier=tier,
    )


# ---------------------------------------------------------------------------
# Cost recording (best-effort, never fails the call)
# ---------------------------------------------------------------------------

def _try_record_cost(
    feature_id: str | None,
    response: OpenRouterResponse,
    operation: str,
) -> None:
    """Record cost to feature event log if feature_id is available."""
    if not feature_id:
        return
    try:
        from app.db import db_session
        from app.models import FeatureRequest
        from app.services.cost_tracker import record_cost

        with db_session() as db:
            feature = db.get(FeatureRequest, feature_id)
            if feature:
                record_cost(
                    db,
                    feature,
                    tier=response.tier.value,
                    model=response.model,
                    tokens_in=response.usage.get("input_tokens", 0),
                    tokens_out=response.usage.get("output_tokens", 0),
                    cost_usd=response.cost_estimate,
                    operation=operation or "unknown",
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("cost recording skipped (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Public API — async
# ---------------------------------------------------------------------------

async def call_openrouter(
    prompt: str,
    tier: ModelTier,
    system_prompt: str | None = None,
    response_format: str = "text",
    model_override: str | None = None,
    feature_id: str | None = None,
    operation: str = "",
) -> OpenRouterResponse:
    """Call OpenRouter with tier-based model routing (async).

    If *feature_id* is provided and a DB session is available, the call cost
    is recorded via :func:`cost_tracker.record_cost`.
    """
    settings = get_settings()
    if not (settings.openrouter_api_key or "").strip():
        raise OpenRouterError(status_code=0, message="OPENROUTER_API_KEY is not configured")

    if settings.openrouter_budget_limit_usd > 0:
        _budget.check(settings.openrouter_budget_limit_usd)

    model = _resolve_model(tier, model_override)
    headers = _build_headers()
    payload = _build_payload(prompt, model, tier, system_prompt, response_format)
    timeout = TIER_TIMEOUTS.get(tier, 60.0)

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
    latency_ms = (time.monotonic() - start) * 1000

    if resp.status_code != 200:
        raise OpenRouterError(
            status_code=resp.status_code,
            message=f"OpenRouter API error {resp.status_code}: {resp.text[:500]}",
        )

    result = _parse_response(resp.json(), tier, model, latency_ms)
    _try_record_cost(feature_id, result, operation)
    return result


# ---------------------------------------------------------------------------
# Public API — sync (for llm_provider.py compatibility)
# ---------------------------------------------------------------------------

def call_openrouter_sync(
    prompt: str,
    tier: ModelTier,
    system_prompt: str | None = None,
    response_format: str = "text",
    model_override: str | None = None,
    feature_id: str | None = None,
    operation: str = "",
) -> OpenRouterResponse:
    """Call OpenRouter with tier-based model routing (sync)."""
    settings = get_settings()
    if not (settings.openrouter_api_key or "").strip():
        raise OpenRouterError(status_code=0, message="OPENROUTER_API_KEY is not configured")

    if settings.openrouter_budget_limit_usd > 0:
        _budget.check(settings.openrouter_budget_limit_usd)

    model = _resolve_model(tier, model_override)
    headers = _build_headers()
    payload = _build_payload(prompt, model, tier, system_prompt, response_format)
    timeout = TIER_TIMEOUTS.get(tier, 60.0)

    start = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(OPENROUTER_API_URL, headers=headers, json=payload)
    latency_ms = (time.monotonic() - start) * 1000

    if resp.status_code != 200:
        raise OpenRouterError(
            status_code=resp.status_code,
            message=f"OpenRouter API error {resp.status_code}: {resp.text[:500]}",
        )

    result = _parse_response(resp.json(), tier, model, latency_ms)
    _try_record_cost(feature_id, result, operation)
    return result
