from __future__ import annotations

from typing import Any


def _lines(values: list[str] | None) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def _non_empty(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _normalized_why_now(*, problem: str, why_now: str) -> str:
    raw = str(why_now or "").strip()
    if not raw:
        return "User requested this now via Slack intake."
    lowered = raw.lower()
    marker = "requested via slack intake. context:"
    if lowered.startswith(marker):
        context = raw.split(":", 1)[1].strip() if ":" in raw else ""
        if context and context.lower() == str(problem or "").strip().lower():
            return "User requested this now via Slack intake."
    return raw


def build_optimized_prompt(spec: dict[str, Any]) -> str:
    """Create a deterministic implementation prompt from intake data."""

    title = _non_empty(spec.get("title"), "Untitled feature")
    problem = _non_empty(spec.get("problem"), "No user problem provided.")
    why_now = _normalized_why_now(problem=problem, why_now=spec.get("business_justification"))
    mode = _non_empty(spec.get("implementation_mode"), "new_feature")
    repo = _non_empty(spec.get("repo"), "(not specified)")
    edit_scope = _non_empty(spec.get("edit_scope"))
    proposed_solution = _non_empty(spec.get("proposed_solution"))

    acceptance = _lines(spec.get("acceptance_criteria"))
    non_goals = _lines(spec.get("non_goals"))
    links = _lines(spec.get("links"))
    source_repos = _lines(spec.get("source_repos"))
    risk_flags = _lines(spec.get("risk_flags"))

    acceptance_lines = "\n".join([f"- {item}" for item in acceptance]) or "- Define measurable acceptance criteria."
    non_goal_lines = "\n".join([f"- {item}" for item in non_goals]) if non_goals else ""
    link_lines = "\n".join([f"- {item}" for item in links]) if links else ""
    source_repo_lines = "\n".join([f"- {item}" for item in source_repos]) if source_repos else ""
    risk_lines = "\n".join([f"- {item}" for item in risk_flags]) if risk_flags else ""

    sections: list[str] = [
        "Build Request",
        f"- Feature: {title}",
        f"- Implementation mode: {mode}",
        f"- Target repo: {repo}",
        "",
        "User context",
        f"- Problem: {problem}",
        f"- Why now: {why_now}",
    ]

    if proposed_solution:
        sections.extend(["", "Preferred approach", f"- {proposed_solution}"])
    if source_repo_lines:
        sections.extend(["", "Reference context", source_repo_lines])
    if edit_scope:
        sections.extend(["", "Edit targeting hints", f"- {edit_scope}"])
    if link_lines:
        sections.extend(["", "Supporting links / attachments", link_lines])

    sections.extend(
        [
            "",
            "Acceptance criteria",
            acceptance_lines,
        ]
    )

    if non_goal_lines:
        sections.extend(["", "Non-goals", non_goal_lines])
    if risk_lines:
        sections.extend(["", "Risk flags", risk_lines])

    sections.extend(
        [
            "",
            "Delivery requirements",
            "- Keep changes scoped to the request.",
            "- Add/update tests when behavior changes.",
            "- Summarize implementation and verification steps.",
        ]
    )
    return "\n".join(sections).strip()


def attach_optimized_prompt(spec: dict[str, Any]) -> dict[str, Any]:
    updated = dict(spec or {})
    updated.pop("ui_feature", None)
    updated.pop("ui_keywords", None)
    updated["optimized_prompt"] = build_optimized_prompt(updated)
    return updated
