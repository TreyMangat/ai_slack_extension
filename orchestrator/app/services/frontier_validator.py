"""Frontier model spec validation.

Calls the FRONTIER tier via OpenRouter to review a feature spec
and decide if it's clear enough to start building.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("feature_factory.frontier_validator")

try:
    from app.services.openrouter_provider import ModelTier
except ImportError:  # pragma: no cover - exercised indirectly via service import guard
    ModelTier = None  # type: ignore[assignment]
    HAS_OPENROUTER_PROVIDER = False
else:
    HAS_OPENROUTER_PROVIDER = True


async def _default_call_openrouter(*args: Any, **kwargs: Any) -> Any:
    from app.services.openrouter_provider import call_openrouter as provider_call_openrouter

    return await provider_call_openrouter(*args, **kwargs)


call_openrouter = _default_call_openrouter


class ValidationResult(BaseModel):
    is_valid: bool = False
    confidence: float = 0.0
    improved_title: str = ""
    improved_problem: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    suggestions: str = ""
    reasoning: str = ""
    model: str = ""
    tier: str = ""
    usage: dict[str, int] = Field(default_factory=dict)
    cost_estimate_usd: float = 0.0


_SYSTEM_PROMPT = """You are a senior product manager reviewing a feature request spec.

Your job: decide if this spec has enough detail for a developer to start building.

REVIEW CRITERIA:
- Is the problem/description clear and specific?
- Is the repo correct and does the request make sense for that repo?
- Can a developer understand WHAT to build from this?

RESPONSE FORMAT (JSON only, no markdown):
{
  "is_valid": true/false,
  "confidence": 0.0-1.0,
  "improved_title": "A better 5-8 word title if the current one is bad",
  "improved_problem": "A cleaner version of the problem statement if needed",
  "acceptance_criteria": ["Specific testable criterion 1", "Criterion 2"],
  "missing_info": ["What's missing, if anything"],
  "suggestions": "Any suggestions for improvement",
  "reasoning": "Why you made this decision"
}

RULES:
- Be pragmatic. If a developer could figure out what to build, it's valid.
- Don't demand excessive detail for simple requests.
- "Add dark mode to settings page" is VALID — it's clear what to build.
- "Make it better" is INVALID — too vague.
- Generate acceptance_criteria ONLY if you can make them specific and useful.
  Don't generate generic ones like "changes are committed as a PR".
- If the title is bad (too long, identical to problem), improve it.
- If improved_title or improved_problem are empty strings, the originals are fine.
"""


def _normalized_result_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    normalized = {k: v for k, v in parsed.items() if k in ValidationResult.model_fields}

    if "is_valid" not in normalized:
        status = str(parsed.get("status", "") or "").strip().upper()
        if status:
            normalized["is_valid"] = status == "READY_FOR_BUILD"

    if "missing_info" not in normalized:
        missing_fields = parsed.get("missing_fields")
        if isinstance(missing_fields, list):
            normalized["missing_info"] = [str(item).strip() for item in missing_fields if str(item).strip()]

    suggestions = parsed.get("suggestions", normalized.get("suggestions", ""))
    if isinstance(suggestions, list):
        normalized["suggestions"] = "\n".join(str(item).strip() for item in suggestions if str(item).strip())
    elif suggestions is None:
        normalized["suggestions"] = ""
    elif isinstance(suggestions, str):
        normalized["suggestions"] = suggestions

    return normalized


async def validate_spec_with_frontier(spec: dict[str, Any]) -> ValidationResult:
    """Ask the frontier model to review a feature spec.

    Returns a ValidationResult. Never raises — returns a failed result
    on any error, so the caller can fall back to basic validation.
    """
    user_prompt = json.dumps(
        {
            "title": spec.get("title", ""),
            "problem": spec.get("problem", ""),
            "repo": spec.get("repo", ""),
            "base_branch": spec.get("base_branch", ""),
            "acceptance_criteria": spec.get("acceptance_criteria", []),
            "implementation_mode": spec.get("implementation_mode", "new_feature"),
        },
        indent=2,
    )

    try:
        if not HAS_OPENROUTER_PROVIDER or ModelTier is None:
            raise RuntimeError("OpenRouter provider unavailable")

        response = await call_openrouter(
            prompt=f"Review this feature spec:\n{user_prompt}",
            tier=ModelTier.FRONTIER,
            system_prompt=_SYSTEM_PROMPT,
            response_format="json_object",
        )
        raw = response.content
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        result = ValidationResult(
            **_normalized_result_payload(parsed if isinstance(parsed, dict) else {}),
            model=str(getattr(response, "model", "") or ""),
            tier=str(getattr(getattr(response, "tier", ""), "value", getattr(response, "tier", "")) or ""),
            usage=dict(getattr(response, "usage", {}) or {}),
            cost_estimate_usd=float(getattr(response, "cost_estimate", 0.0) or 0.0),
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("frontier_validation_failed: %s", exc)
        return ValidationResult(
            is_valid=False,
            confidence=0.0,
            reasoning=f"Frontier validation error: {exc}",
        )


def validate_spec_with_frontier_sync(spec: dict[str, Any]) -> ValidationResult:
    """Synchronous wrapper for validate_spec_with_frontier."""
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, validate_spec_with_frontier(spec)).result()
    return asyncio.run(validate_spec_with_frontier(spec))
