from __future__ import annotations

from typing import Any


def _lines(values: list[str] | None) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def _non_empty(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def detect_ui_feature(spec: dict[str, Any]) -> tuple[bool, list[str]]:
    # UI-specific routing is intentionally disabled so all requests follow the same path.
    return (False, [])


def build_optimized_prompt(spec: dict[str, Any]) -> str:
    """Create a deterministic implementation prompt from intake data."""

    title = _non_empty(spec.get("title"), "Untitled feature")
    problem = _non_empty(spec.get("problem"), "No user problem provided.")
    why_now = _non_empty(spec.get("business_justification"), "No urgency/business context provided.")
    mode = _non_empty(spec.get("implementation_mode"), "new_feature")
    repo = _non_empty(spec.get("repo"), "(not specified)")
    proposed_solution = _non_empty(spec.get("proposed_solution"))

    acceptance = _lines(spec.get("acceptance_criteria"))
    non_goals = _lines(spec.get("non_goals"))
    links = _lines(spec.get("links"))
    source_repos = _lines(spec.get("source_repos"))
    risk_flags = _lines(spec.get("risk_flags"))

    acceptance_lines = "\n".join([f"- {item}" for item in acceptance]) or "- Define measurable acceptance criteria."
    non_goal_lines = "\n".join([f"- {item}" for item in non_goals]) or "- None specified."
    link_lines = "\n".join([f"- {item}" for item in links]) or "- None provided."
    source_repo_lines = "\n".join([f"- {item}" for item in source_repos]) or "- None provided."
    risk_lines = "\n".join([f"- {item}" for item in risk_flags]) or "- No explicit high-risk flags provided."

    solution_line = ""
    if proposed_solution:
        solution_line = f"\nPreferred approach:\n- {proposed_solution}\n"

    return (
        "Build Request\n"
        f"- Feature: {title}\n"
        f"- Implementation mode: {mode}\n"
        f"- Target repo: {repo}\n\n"
        "User context\n"
        f"- Problem: {problem}\n"
        f"- Why now: {why_now}\n"
        f"{solution_line}\n"
        "Reference context\n"
        f"{source_repo_lines}\n\n"
        "Supporting links / attachments\n"
        f"{link_lines}\n\n"
        "Acceptance criteria\n"
        f"{acceptance_lines}\n\n"
        "Non-goals\n"
        f"{non_goal_lines}\n\n"
        "Risk flags\n"
        f"{risk_lines}\n\n"
        "Delivery requirements\n"
        "- Keep changes scoped to the request.\n"
        "- Add/update tests when behavior changes.\n"
        "- Summarize implementation and verification steps.\n"
    ).strip()


def attach_optimized_prompt(spec: dict[str, Any]) -> dict[str, Any]:
    updated = dict(spec or {})
    updated.pop("ui_feature", None)
    updated.pop("ui_keywords", None)
    updated["optimized_prompt"] = build_optimized_prompt(updated)
    return updated
