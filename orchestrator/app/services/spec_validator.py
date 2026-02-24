from __future__ import annotations

from typing import Any


REQUIRED_FIELDS = ["title", "problem", "business_justification"]
COMPLETION_WEIGHTS = {
    "title": 20,
    "problem": 20,
    "business_justification": 15,
    "acceptance_criteria": 20,
    "repo": 15,
    "implementation_mode": 10,
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

    acceptance = spec.get("acceptance_criteria") or []
    if not isinstance(acceptance, list) or len([a for a in acceptance if str(a).strip()]) == 0:
        missing.append("acceptance_criteria")

    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    if mode not in {"new_feature", "reuse_existing"}:
        missing.append("implementation_mode")

    source_repos = spec.get("source_repos") or []
    if mode == "reuse_existing":
        if not isinstance(source_repos, list) or len([r for r in source_repos if str(r).strip()]) == 0:
            missing.append("source_repos")
        if not str(spec.get("repo", "")).strip():
            warnings.append("repo is empty; first source repo will be treated as execution target")
    elif isinstance(source_repos, list) and any(str(r).strip() for r in source_repos):
        warnings.append("source_repos provided for new_feature mode; they will be treated as references only")

    # Warnings (not blockers)
    if not str(spec.get("repo", "")).strip():
        warnings.append("repo is empty (OK for local mock mode)")

    risk_flags = spec.get("risk_flags") or []
    if isinstance(risk_flags, list) and any(str(x).lower() in {"payments", "auth", "migrations"} for x in risk_flags):
        warnings.append("High-risk flag detected: consider requiring human review")

    return (len(missing) == 0, missing, warnings)


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
