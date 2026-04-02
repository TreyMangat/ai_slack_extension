from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ["title", "problem", "repo"]
COMPLETION_WEIGHTS = {
    "title": 30,
    "problem": 30,
    "repo": 20,
    "implementation_mode": 10,
    "business_justification": 5,
    "acceptance_criteria": 5,
}


def validate_spec(spec: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """Validate a feature spec.

    Returns:
        (is_valid, missing_fields, warnings)

    This is intentionally conservative:
    - we want to force clarity before the code runner starts
    """

    missing: list[str] = []
    warnings: list[str] = []

    for f in REQUIRED_FIELDS:
        if not str(spec.get(f, "")).strip():
            missing.append(f)

    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    if mode not in {"new_feature", "reuse_existing"}:
        missing.append("implementation_mode")

    source_repos = spec.get("source_repos") or []
    if mode == "reuse_existing":
        if not isinstance(source_repos, list) or len([r for r in source_repos if str(r).strip()]) == 0:
            missing.append("source_repos")
    elif isinstance(source_repos, list) and any(str(r).strip() for r in source_repos):
        warnings.append("source_repos provided for new_feature mode; they will be treated as references only")

    risk_flags = spec.get("risk_flags") or []
    if isinstance(risk_flags, list) and any(str(x).lower() in {"payments", "auth", "migrations"} for x in risk_flags):
        warnings.append("High-risk flag detected: consider requiring human review")

    return (len(missing) == 0, missing, warnings)


# ---------------------------------------------------------------------------
# LLM-enhanced spec validation (FRONTIER tier via OpenRouter)
# ---------------------------------------------------------------------------

_SPEC_VALIDATION_SYSTEM_PROMPT = (
    "You are a senior product manager reviewing a feature request specification. "
    "Evaluate whether the spec is complete enough to begin implementation.\n\n"
    "Return JSON only with these keys:\n"
    '  status: "READY_FOR_BUILD" or "NEEDS_INFO"\n'
    "  missing_fields: list of field names that are missing or too vague\n"
    "  suggestions: list of actionable suggestions to improve the spec\n"
    "  confidence: float 0.0-1.0 indicating your confidence in the assessment"
)


async def validate_spec_with_llm(
    spec: dict[str, Any],
    feature_id: str | None = None,
) -> tuple[bool, list[str], list[str], dict[str, Any] | None]:
    """Enhanced spec validation using FRONTIER tier LLM.

    Returns ``(is_valid, missing_fields, suggestions, llm_analysis_dict)``.
    The fourth element is the raw LLM analysis suitable for storing on
    ``FeatureRequest.llm_spec_analysis``, or ``None`` if LLM was not used.

    Falls back to rule-based ``validate_spec`` on any failure (missing key,
    provider error, parse error, etc.).  Never blocks the pipeline.
    """
    settings = get_settings()
    if not (settings.openrouter_api_key or "").strip():
        is_valid, missing, warnings = validate_spec(spec)
        return (is_valid, missing, warnings, None)

    try:
        from app.services.openrouter_provider import (
            ModelTier,
            call_openrouter,
        )

        spec_summary = json.dumps(
            {k: v for k, v in spec.items() if v},
            indent=2,
            default=str,
        )
        prompt = f"Evaluate this feature request spec:\n\n```json\n{spec_summary}\n```"

        response = await call_openrouter(
            prompt=prompt,
            tier=ModelTier.FRONTIER,
            system_prompt=_SPEC_VALIDATION_SYSTEM_PROMPT,
            response_format="json_object",
        )

        parsed = json.loads(response.content) if isinstance(response.content, str) else response.content
        logger.info(
            "spec_validator_llm",
            extra={
                "model": response.model,
                "cost_estimate_usd": round(response.cost_estimate, 6),
                "llm_status": parsed.get("status"),
                "confidence": parsed.get("confidence"),
            },
        )

        status = parsed.get("status", "NEEDS_INFO")
        missing = parsed.get("missing_fields", [])
        suggestions = parsed.get("suggestions", [])
        confidence = parsed.get("confidence", 0.0)
        is_valid = status == "READY_FOR_BUILD"

        llm_analysis = {
            "model": response.model,
            "tier": response.tier.value,
            "status": status,
            "missing_fields": missing,
            "suggestions": suggestions,
            "confidence": confidence,
            "usage": dict(response.usage or {}),
            "cost_estimate_usd": round(float(response.cost_estimate or 0.0), 6),
        }

        return (is_valid, missing, suggestions, llm_analysis)

    except Exception as exc:  # noqa: BLE001
        logger.warning("spec_validator: LLM validation failed, falling back to rule-based: %s", exc)
        is_valid, missing, warnings = validate_spec(spec)
        return (is_valid, missing, warnings, None)


def validate_spec_with_llm_sync(
    spec: dict[str, Any],
    feature_id: str | None = None,
) -> tuple[bool, list[str], list[str], dict[str, Any] | None]:
    _ = feature_id
    return asyncio.run(validate_spec_with_llm(spec, feature_id=feature_id))


def spec_completion_report(spec: dict[str, Any]) -> dict[str, Any]:
    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    acceptance = spec.get("acceptance_criteria") or []
    source_repos = spec.get("source_repos") or []
    risk_flags = [str(x).strip().lower() for x in (spec.get("risk_flags") or []) if str(x).strip()]

    checks: dict[str, bool] = {
        "title": bool(str(spec.get("title") or "").strip()),
        "problem": bool(str(spec.get("problem") or "").strip()),
        "business_justification": bool(str(spec.get("business_justification") or "").strip()),
        "acceptance_criteria": bool(isinstance(acceptance, list) and any(str(x).strip() for x in acceptance)),
        "repo": bool(str(spec.get("repo") or "").strip()),
        "implementation_mode": mode in {"new_feature", "reuse_existing"},
    }
    checks["source_repos"] = not (mode == "reuse_existing" and not any(str(x).strip() for x in source_repos))

    score = 0
    for field, weight in COMPLETION_WEIGHTS.items():
        if checks.get(field):
            score += int(weight)
    score = max(min(int(score), 100), 0)

    return {
        "score": score,
        "checks": checks,
        "high_risk": any(flag in {"auth", "payments", "billing", "migrations"} for flag in risk_flags),
    }
