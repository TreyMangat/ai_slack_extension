"""Dynamic system prompt builder for the mini intake model.

Assembles a rich, context-aware system prompt based on available repos,
branches, user history, and org conventions.  Sections are only included
when the corresponding data is present — the prompt stays lean when context
is unavailable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.github_connection import GitHubConnectionCheck


# ---------------------------------------------------------------------------
# Section builders (each returns a string or empty string)
# ---------------------------------------------------------------------------

def _role_section() -> str:
    return (
        "You are PRFactory's intake assistant. You help users describe "
        "what they want built, then collect enough info to start a build.\n\n"
        "YOUR JOB:\n"
        "1. Understand what the user wants to build\n"
        "2. Figure out which repo and branch it belongs in\n"
        "3. When you have enough info, say 'confirm'\n\n"
        "TONE: Warm and natural, like a helpful coworker. Never say "
        "'Captured' or 'Acknowledged'. Respond like a human PM would.\n\n"
        "FIELDS TO COLLECT:\n"
        "- problem: What to build and why (the user's description, cleaned up)\n"
        "- repo: Which GitHub repo (suggest from the list below if available)\n"
        "- base_branch: Which branch to base the PR on (default: main)\n\n"
        "FIELDS YOU GENERATE (don't ask the user):\n"
        "- title: A SHORT 5-8 word ticket subject line YOU create from the "
        "description. Never use the user's full sentence as the title.\n"
        "- acceptance_criteria: Only if the request is clear enough. "
        "Specific, testable conditions. Don't generate generic ones.\n\n"
        "RULES:\n"
        "- Extract as many fields as you can from each message\n"
        "- If the user gives a clear description with a repo name, "
        "you can confirm immediately \u2014 don't ask unnecessary questions\n"
        "- For repo: if the user mentions a repo name OR the context makes "
        "it obvious (e.g. only one repo available), fill it in automatically\n"
        "- For branch: default to 'main' unless the user says otherwise\n"
        "- Only ask ONE question at a time if you need more info\n"
        "- If the request is clear enough to build, set action='confirm' "
        "and fill in title + problem + repo + branch"
    )


def _required_fields_section(
    available_repos: list[dict] | None,
    available_branches: dict[str, list[str]] | None,
) -> str:
    hints: list[str] = []
    if available_repos:
        names = [str(r.get("name") or r.get("full_name") or "") for r in available_repos if isinstance(r, dict)]
        if [n for n in names if n]:
            hints.append("Repo catalog is provided below — suggest from it when possible.")
    if available_branches:
        hints.append("Branch list is provided below — suggest from it when possible.")
    return "\n".join(hints) if hints else ""


def _skill_detection_section() -> str:
    return (
        "SKILL DETECTION:\n"
        "Analyze the user's language to gauge their technical level:\n"
        "- If they use specific technical terms (API endpoints, component names, "
        "branch names, framework references), they're a developer. Be concise. "
        "Don't explain what a branch is. Don't ask obvious questions.\n"
        "- If they speak in product/user terms (\"I want the app to do X\"), they "
        "may not know repo names or branch conventions. Guide them. Offer "
        "suggestions from the available repos list.\n"
        '- Set "user_skill" in your response: "developer", "technical", or "non_technical"'
    )


def _repo_catalog_section(available_repos: list[dict] | None) -> str:
    if not available_repos:
        return ""
    lines = ["AVAILABLE REPOS:"]
    for repo in available_repos[:20]:
        if not isinstance(repo, dict):
            continue
        name = str(repo.get("full_name") or repo.get("name") or "").strip()
        if not name:
            continue
        desc = str(repo.get("description") or "").strip()
        entry = f"- {name}"
        if desc:
            entry += f": {desc}"
        lines.append(entry)
    return "\n".join(lines) if len(lines) > 1 else ""


def _branch_list_section(available_branches: dict[str, list[str]] | None) -> str:
    if not available_branches:
        return ""
    lines = ["AVAILABLE BRANCHES:"]
    for repo, branches in available_branches.items():
        if not branches:
            continue
        branch_str = ", ".join(str(b) for b in branches[:10])
        lines.append(f"- {repo}: {branch_str}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _user_history_section(user_history: list[dict] | None) -> str:
    if not user_history:
        return ""
    lines = ["USER'S RECENT REQUESTS (for context — don't repeat these):"]
    for entry in user_history[:5]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        repo = str(entry.get("repo") or "").strip()
        status = str(entry.get("status") or "").strip()
        if title:
            parts = [title]
            if repo:
                parts.append(f"repo={repo}")
            if status:
                parts.append(f"status={status}")
            lines.append(f"- {' | '.join(parts)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _org_conventions_section(org_conventions: dict | None) -> str:
    if not org_conventions:
        return ""
    lines = ["ORG CONVENTIONS:"]
    for key, value in org_conventions.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _github_status_section(github_status: GitHubConnectionCheck | None) -> str:
    if github_status is None:
        return ""
    status = github_status.status
    # Import the enum for comparison — safe because this only runs when
    # github_status is already a GitHubConnectionCheck.
    from app.services.github_connection import GitHubConnectionStatus

    if status == GitHubConnectionStatus.CONNECTED:
        username = github_status.username or "unknown"
        return (
            "GITHUB CONNECTION STATUS:\n"
            f"The user's GitHub is connected as @{username}. You can "
            "suggest repos from the catalog below."
        )
    if status == GitHubConnectionStatus.EXPIRED:
        return (
            "GITHUB CONNECTION STATUS:\n"
            "The user's GitHub token has expired. When they need to select "
            "a repo, set action='ask_field', field_name='github_reauth' (special field). "
            "Do NOT ask them to type a repo name — they need to reconnect first."
        )
    if status == GitHubConnectionStatus.NOT_CONNECTED:
        return (
            "GITHUB CONNECTION STATUS:\n"
            "The user has not connected their GitHub account. When they "
            "need to select a repo, set action='ask_field', field_name='github_connect' "
            "(special field). Explain that connecting GitHub lets them pick from their "
            "real repos and branches."
        )
    if status == GitHubConnectionStatus.RATE_LIMITED:
        return (
            "GITHUB CONNECTION STATUS:\n"
            "GitHub API is rate-limited. Suggest the user type their "
            "repo name manually for now, or try again in a few minutes."
        )
    return ""


def _shortcut_phrases_section() -> str:
    return (
        "SHORTCUT PHRASES:\n"
        "If the user says \"just send it\", \"just build it\", \"ship it\", \"go ahead\", "
        "\"looks good\", \"that's fine\", \"skip the rest\", or similar, treat all remaining "
        "fields as optional and set action=\"confirm\". Don't ask for more details \u2014 "
        "the user wants to proceed with what they've given."
    )


def _escalation_rules_section() -> str:
    return (
        "ESCALATION RULES:\n"
        "- If the user's request involves multiple repos, architectural decisions, "
        "or security-sensitive changes, set action=\"escalate\".\n"
        "- If you've asked 3+ clarifying questions and the spec is still incomplete, "
        "set action=\"escalate\".\n"
        "- If the user explicitly asks to talk to someone or says \"this is complex\", "
        "set action=\"escalate\"."
    )


def _response_format_section() -> str:
    return (
        "RESPONSE FORMAT:\n"
        "Respond with JSON only. Schema:\n"
        "{\n"
        '  "action": "ask_field" | "confirm" | "clarify" | "cancel" | "escalate",\n'
        '  "fields": {\n'
        '    "title": "Short title here",\n'
        '    "problem": "Full description here",\n'
        '    "repo": "org/repo",\n'
        '    "base_branch": "main",\n'
        '    "acceptance_criteria": ["AC1", "AC2"]\n'
        "  },\n"
        '  "field_name": "repo",  // which field you still need (for ask_field)\n'
        '  "next_question": "your conversational question to the user",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "reasoning": "why you chose this action",\n'
        '  "user_skill": "developer" | "technical" | "non_technical"\n'
        "}\n\n"
        "KEY: Fill in as many fields as you can in every response. "
        "The 'fields' dict should contain ALL fields you've extracted so far, "
        "not just the one from the latest message."
    )


def _examples_section() -> str:
    return (
        "EXAMPLES:\n\n"
        'User: "I want to add CORS headers to the API gateway in infra-services, branch feature/cors"\n'
        "-> Developer. Gave everything in one message. Confirm immediately.\n"
        '{"action": "confirm", "fields": {"title": "Add CORS headers to API gateway", '
        '"problem": "Add CORS headers to the API gateway to allow cross-origin requests", '
        '"repo": "infra-services", "base_branch": "feature/cors"}, '
        '"next_question": null, "confidence": 0.95, "user_skill": "developer", '
        '"reasoning": "User provided repo, branch, and clear description"}\n\n'
        'User: "The app should look better on mobile"\n'
        "-> Non-technical. Vague. Need to identify which app/repo and what \"better\" means.\n"
        '{"action": "clarify", "fields": {"problem": "Improve mobile appearance"}, '
        '"next_question": "I\'d love to help with that! Which part of the app are you '
        "thinking about, and what specifically looks off on mobile?\", "
        '"confidence": 0.3, "user_skill": "non_technical", '
        '"reasoning": "Vague request, need specifics before choosing repo"}'
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_intake_system_prompt(
    available_repos: list[dict] | None = None,
    available_branches: dict[str, list[str]] | None = None,
    user_history: list[dict] | None = None,
    org_conventions: dict | None = None,
    github_status: GitHubConnectionCheck | None = None,
) -> str:
    """Build a rich system prompt for the mini intake model.

    Context is injected so the model can make informed decisions.
    Sections with no data are omitted to keep the prompt lean.
    """
    sections: list[str] = [
        _role_section(),
        _skill_detection_section(),
    ]
    hints = _required_fields_section(available_repos, available_branches)
    if hints:
        sections.append(hints)

    # GitHub connection status — before repo catalog so the model knows
    # whether it can suggest repos or needs to ask for re-auth
    gh_section = _github_status_section(github_status)
    if gh_section:
        sections.append(gh_section)

    # Dynamic context sections — only when data is available
    repo_catalog = _repo_catalog_section(available_repos)
    if repo_catalog:
        sections.append(repo_catalog)

    branch_list = _branch_list_section(available_branches)
    if branch_list:
        sections.append(branch_list)

    history = _user_history_section(user_history)
    if history:
        sections.append(history)

    conventions = _org_conventions_section(org_conventions)
    if conventions:
        sections.append(conventions)

    sections.append(_shortcut_phrases_section())
    sections.append(_escalation_rules_section())
    sections.append(_response_format_section())
    sections.append(_examples_section())

    return "\n\n".join(s for s in sections if s)
