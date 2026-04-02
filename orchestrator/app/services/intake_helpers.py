"""Pure data-transformation helpers for Slack intake flows.

Extracted from slackbot.py — no Slack client calls, only data
manipulation and validation.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.github_repo import parse_repo_slug

INTAKE_MODE_NORMAL = "normal"
INTAKE_MODE_DEVELOPER = "developer"

QUESTION_BY_FIELD: dict[str, str] = {
    "title": "How can I help you?",
    "problem": "Describe what you want in one short paragraph (what to build + why).",
    "business_justification": "Why is this needed now?",
    "links": "Optional: share links/files in this thread, or reply `skip`.",
    "repo": "Do you know what project/repo this belongs to? Reply with `org/repo`, repo URL, or `unsure`.",
    "base_branch": "Optional: which base branch should we open the PR against? Reply with branch name, or `skip`.",
    "implementation_mode": "Should implementation start from scratch or reuse existing project patterns? Reply `scratch` or `reuse`.",
    "source_repos": "If reusing existing patterns, which repos should be references? One per line.",
    "edit_scope": "For edit mode, what files/modules/symbols should I touch first? (one short reply, or `skip`)",
    "proposed_solution": "Any preferred implementation approach or constraints? Reply `skip` if none.",
    "acceptance_criteria": "Optional: acceptance criteria, one per line. Reply `skip` to leave blank.",
}

CREATE_FLOW_FIELDS_MINIMAL = [
    "title",
    "repo",
    "base_branch",
]
CREATE_FLOW_FIELDS_FULL = [
    "title",
    "implementation_mode",
    "edit_scope",
    "problem",
    "repo",
    "base_branch",
    "acceptance_criteria",
    "links",
]

UPDATE_FALLBACK_FIELDS = [
    "repo",
    "base_branch",
    "implementation_mode",
    "edit_scope",
    "source_repos",
    "problem",
    "business_justification",
    "acceptance_criteria",
    "proposed_solution",
    "links",
]

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
SKIP_TOKENS = {"skip", "n/a", "na", "none", "no", "not sure", "unsure", "unknown", "idk"}

AFFIRMATION_PHRASES = {
    "yes", "yeah", "yep", "yup", "correct", "right", "that's right",
    "thats right", "that one", "the right one", "yes thats right",
    "yes that's right", "ya", "sure", "ok", "okay", "confirmed",
}


def parse_lines(text: str) -> list[str]:
    return [line.strip().lstrip("- ").strip() for line in (text or "").splitlines() if line.strip()]


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in values:
        item = str(raw).strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def extract_urls(text: str) -> list[str]:
    return dedupe(URL_RE.findall(text or ""))


def extract_file_links(event: dict[str, Any]) -> list[str]:
    links: list[str] = []
    for item in event.get("files") or []:
        if not isinstance(item, dict):
            continue
        permalink = str(item.get("permalink") or "").strip()
        if permalink:
            links.append(permalink)
    return dedupe(links)


def is_skip(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in SKIP_TOKENS


def is_stop_command(text: str) -> bool:
    token = re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())
    return token == "stop"


def normalize_branch_name(text: str) -> str:
    branch = str(text or "").splitlines()[0].strip()
    branch = branch.strip("`").strip()
    if branch.lower().startswith("refs/heads/"):
        branch = branch[11:].strip()
    return branch


def normalize_mode(text: str) -> str:
    value = (text or "").strip().lower()
    if value in {"scratch", "new", "new_feature", "from scratch", "build from scratch"}:
        return "new_feature"
    if value in {"reuse", "existing", "reuse_existing", "use existing", "existing patterns"}:
        return "reuse_existing"
    if "scratch" in value:
        return "new_feature"
    if "reuse" in value or "existing" in value:
        return "reuse_existing"
    return ""


def format_mode(mode: str) -> str:
    if mode == "reuse_existing":
        return "Reuse existing repo patterns"
    return "Build in target repo (default)"


def normalize_intake_mode(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized == INTAKE_MODE_DEVELOPER:
        return INTAKE_MODE_DEVELOPER
    return INTAKE_MODE_NORMAL


def session_intake_mode(session: Any) -> str:
    return normalize_intake_mode(str((session.answers or {}).get("_intake_mode") or INTAKE_MODE_NORMAL))


def set_session_intake_mode(session: Any, mode: str) -> None:
    session.answers["_intake_mode"] = normalize_intake_mode(mode)


def intake_mode_label(mode: str) -> str:
    return "Developer" if normalize_intake_mode(mode) == INTAKE_MODE_DEVELOPER else "Normal"


def intake_mode_toggle_label(mode: str) -> str:
    current = normalize_intake_mode(mode)
    if current == INTAKE_MODE_DEVELOPER:
        return "Switch to Normal"
    return "Switch to Developer"


def default_spec() -> dict[str, Any]:
    return {
        "title": "",
        "problem": "",
        "business_justification": "",
        "proposed_solution": "",
        "acceptance_criteria": [],
        "non_goals": [],
        "repo": "",
        "base_branch": "",
        "implementation_mode": "new_feature",
        "source_repos": [],
        "edit_scope": "",
        "risk_flags": [],
        "links": [],
        "debug_build": False,
    }


def capture_field_answer(
    session: Any,
    *,
    field: str,
    event: dict[str, Any],
    require_repo: bool = False,
) -> tuple[bool, str]:
    text = str(event.get("text") or "").strip()
    file_links = extract_file_links(event)

    if field in {"title", "problem", "business_justification"}:
        if not text or is_skip(text):
            return False, "That field is required before build. Please provide a short answer."
        session.answers[field] = text
        session.asked_fields.add(field)
        return True, "Captured."

    if field == "links":
        links = dedupe(extract_urls(text) + file_links)
        if links:
            existing = [str(x).strip() for x in (session.answers.get("links") or []) if str(x).strip()]
            session.answers["links"] = dedupe(existing + links)
            session.asked_fields.add("links")
            return True, f"Saved {len(links)} link(s)/attachment(s)."
        return True, "No links added."

    if field == "repo":
        if not text or is_skip(text):
            if require_repo and session_intake_mode(session) != INTAKE_MODE_DEVELOPER:
                return False, "Target repo is required. Reply with `org/repo`."
            session.answers["repo"] = ""
            return True, "Repo left unspecified."
        repo_input = text.splitlines()[0].strip()
        owner, repo = parse_repo_slug(repo_input)
        if owner and repo:
            repo_input = f"{owner}/{repo}"
        session.answers["repo"] = repo_input
        session.asked_fields.add("repo")
        return True, "Captured repo/project."

    if field == "base_branch":
        if not text or is_skip(text):
            session.answers["base_branch"] = ""
            return True, "Using default branch."
        branch = normalize_branch_name(text)
        if not re.match(r"^[A-Za-z0-9._/-]+$", branch):
            return False, "Branch name can only include letters, numbers, `.`, `_`, `/`, and `-`."
        session.answers["base_branch"] = branch
        session.asked_fields.add("base_branch")
        return True, f"Base branch set to `{branch}`."

    if field == "implementation_mode":
        mode = normalize_mode(text)
        if not mode:
            return False, "Please reply with `scratch` or `reuse`."
        session.answers["implementation_mode"] = mode
        if mode != "reuse_existing":
            session.answers.pop("source_repos", None)
            session.answers.pop("edit_scope", None)
            session.asked_fields.discard("source_repos")
            session.asked_fields.discard("edit_scope")
        session.asked_fields.add("implementation_mode")
        return True, f"Using mode: `{mode}`."

    if field == "source_repos":
        mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
        if mode != "reuse_existing":
            return True, "Reuse references not needed for scratch mode."

        if is_skip(text) or not text:
            session.answers["source_repos"] = []
            session.asked_fields.add("source_repos")
            return True, "No external reference repos provided; I will use the target repo context only."

        repos = parse_lines(text)
        if not repos:
            return False, "Reuse mode needs at least one reference repo. Please provide one per line."
        session.answers["source_repos"] = repos
        session.asked_fields.add("source_repos")
        return True, f"Saved {len(repos)} source repo reference(s)."

    if field == "edit_scope":
        mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
        if mode != "reuse_existing":
            return True, "Edit targeting details are not needed for scratch mode."
        if not text or is_skip(text):
            session.answers["edit_scope"] = "Focus on existing modules and files most directly related to the request."
            session.asked_fields.add("edit_scope")
            return True, "No explicit edit target provided; I will infer likely files from context."
        session.answers["edit_scope"] = text
        session.asked_fields.add("edit_scope")
        return True, "Captured edit targeting details."

    if field == "proposed_solution":
        if not text or is_skip(text):
            return True, "No preferred implementation approach captured."
        session.answers["proposed_solution"] = text
        session.asked_fields.add("proposed_solution")
        return True, "Captured implementation notes."

    if field == "acceptance_criteria":
        criteria = parse_lines(text)
        if (not criteria and is_skip(text)) or (not criteria and not text):
            session.answers["acceptance_criteria"] = []
            return True, "Acceptance criteria left blank for now."
        if not criteria:
            return False, "Please provide acceptance criteria lines or reply `skip`."
        session.answers["acceptance_criteria"] = criteria
        session.asked_fields.add("acceptance_criteria")
        return True, f"Captured {len(criteria)} acceptance criteria item(s)."

    return False, f"Unsupported intake field `{field}`."


def create_spec_from_session(session: Any) -> dict[str, Any]:
    spec = default_spec()
    spec.update(session.answers)
    for key in [item for item in spec.keys() if str(item).startswith("_")]:
        spec.pop(key, None)

    spec["title"] = str(spec.get("title") or "").strip()
    spec["problem"] = str(spec.get("problem") or "").strip()
    spec["business_justification"] = str(spec.get("business_justification") or "").strip()
    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    spec["implementation_mode"] = mode
    spec["repo"] = str(spec.get("repo") or "").strip()
    spec["base_branch"] = str(spec.get("base_branch") or "").strip()
    spec["edit_scope"] = str(spec.get("edit_scope") or "").strip()
    raw_source_repos = spec.get("source_repos") or []
    if isinstance(raw_source_repos, str):
        spec["source_repos"] = parse_lines(raw_source_repos)
    else:
        spec["source_repos"] = [str(x).strip() for x in raw_source_repos if str(x).strip()]
    spec["links"] = [str(x).strip() for x in (spec.get("links") or []) if str(x).strip()]
    raw_acceptance_criteria = spec.get("acceptance_criteria") or []
    if isinstance(raw_acceptance_criteria, str):
        criteria = parse_lines(raw_acceptance_criteria)
    else:
        criteria = [str(x).strip() for x in raw_acceptance_criteria if str(x).strip()]
    spec["acceptance_criteria"] = criteria

    if mode == "reuse_existing" and not str(spec.get("edit_scope") or "").strip():
        spec["edit_scope"] = "Focus on existing modules and files that directly implement this request."

    return spec


def update_patch_from_session(session: Any) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field in sorted(session.asked_fields):
        if field in session.answers:
            patch[field] = session.answers[field]
    return patch
