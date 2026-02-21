from __future__ import annotations

import re
from typing import Any


def _lines(values: list[str] | None) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def _non_empty(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


UI_KEYWORDS: tuple[str, ...] = (
    "ui",
    "user interface",
    "frontend",
    "front-end",
    "website",
    "web app",
    "page",
    "screen",
    "component",
    "layout",
    "button",
    "form",
    "modal",
    "navigation",
    "navbar",
    "sidebar",
    "dashboard",
    "style",
    "styling",
    "css",
    "theme",
    "visual",
)


def detect_ui_feature(spec: dict[str, Any]) -> tuple[bool, list[str]]:
    text_chunks: list[str] = []
    for key in ["title", "problem", "business_justification", "proposed_solution", "repo"]:
        value = str(spec.get(key) or "").strip()
        if value:
            text_chunks.append(value)

    for key in ["acceptance_criteria", "links", "source_repos", "risk_flags"]:
        values = spec.get(key) or []
        if isinstance(values, list):
            text_chunks.extend([str(v).strip() for v in values if str(v).strip()])

    haystack = "\n".join(text_chunks).lower()
    matched: list[str] = []
    for keyword in UI_KEYWORDS:
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, haystack):
            matched.append(keyword)

    unique = sorted(set(matched))
    return (len(unique) > 0, unique)


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
    ui_feature, ui_keywords = detect_ui_feature(spec)

    acceptance_lines = "\n".join([f"- {item}" for item in acceptance]) or "- Define measurable acceptance criteria."
    non_goal_lines = "\n".join([f"- {item}" for item in non_goals]) or "- None specified."
    link_lines = "\n".join([f"- {item}" for item in links]) or "- None provided."
    source_repo_lines = "\n".join([f"- {item}" for item in source_repos]) or "- None provided."
    risk_lines = "\n".join([f"- {item}" for item in risk_flags]) or "- No explicit high-risk flags provided."
    ui_keywords_line = ", ".join(ui_keywords) if ui_keywords else "(none)"
    ui_hint = "yes" if ui_feature else "no"

    solution_line = ""
    if proposed_solution:
        solution_line = f"\nPreferred approach:\n- {proposed_solution}\n"

    ui_requirements = ""
    if ui_feature:
        ui_requirements = (
            "\nUI delivery requirements\n"
            "- Ensure reviewers can click a preview from the PR checks/deployments.\n"
            "- If no frontend scaffold exists, create a minimal Vite + React app under `web/`.\n"
            "- Include `dev`, `build`, and `preview` npm scripts.\n"
            "- Implement a simple demo page matching the requested UI behavior.\n"
            "- Keep backend integration mocked/static unless explicitly requested.\n"
        )

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
        "Request classification\n"
        f"- UI feature: {ui_hint}\n"
        f"- Matched UI keywords: {ui_keywords_line}\n"
        f"{ui_requirements}\n"
        "Delivery requirements\n"
        "- Keep changes scoped to the request.\n"
        "- Add/update tests when behavior changes.\n"
        "- Summarize implementation and verification steps.\n"
    ).strip()


def attach_optimized_prompt(spec: dict[str, Any]) -> dict[str, Any]:
    updated = dict(spec or {})
    ui_feature, ui_keywords = detect_ui_feature(updated)
    updated["ui_feature"] = bool(ui_feature)
    updated["ui_keywords"] = ui_keywords
    updated["optimized_prompt"] = build_optimized_prompt(updated)
    return updated
