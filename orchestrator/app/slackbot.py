from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from rich.console import Console
from sqlalchemy import delete

from app.config import get_settings
from app.db import db_session
from app.models import SlackIntakeSession
from app.services.github_repo import parse_repo_slug
from app.services.github_user_oauth import has_github_user_connection, resolve_github_user_access_token
from app.services.slack_oauth import get_slack_oauth_runtime
from app.services.reviewer_service import is_approver_allowed


console = Console()

QUESTION_BY_FIELD: dict[str, str] = {
    "title": "How can I help you?",
    "problem": "Describe what you want in one short paragraph (what to build + why).",
    "business_justification": "Why is this needed now?",
    "links": "Optional: share links/files in this thread, or reply `skip`.",
    "repo": "Do you know what project/repo this belongs to? Reply with `org/repo`, repo URL, or `unsure`.",
    "base_branch": "Optional: which base branch should we open the PR against? Reply with branch name, or `skip`.",
    "implementation_mode": "Should implementation start from scratch or reuse existing project patterns? Reply `scratch` or `reuse`.",
    "source_repos": "If reusing existing patterns, which repos should be references? One per line.",
    "proposed_solution": "Any preferred implementation approach or constraints? Reply `skip` if none.",
    "acceptance_criteria": "Optional: acceptance criteria, one per line. Reply `skip` to use defaults.",
}

CREATE_FLOW_FIELDS_MINIMAL = [
    "title",
    "repo",
    "base_branch",
]
CREATE_FLOW_FIELDS_FULL = [
    "title",
    "problem",
    "repo",
    "base_branch",
    "acceptance_criteria",
    "links",
]

UPDATE_FALLBACK_FIELDS = [
    "problem",
    "business_justification",
    "acceptance_criteria",
    "proposed_solution",
    "links",
    "repo",
]

SESSION_TTL_SECONDS = 2 * 60 * 60
APP_HOME_WELCOME_TTL_SECONDS = 6 * 60 * 60
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
SKIP_TOKENS = {"skip", "n/a", "na", "none", "no", "not sure", "unsure", "unknown", "idk"}
PRIMARY_SLASH_COMMAND = "/prfactory"
LEGACY_SLASH_COMMAND = "/feature"
GITHUB_HELP_SLASH_COMMAND = "/prfactory-github"
INTAKE_MODE_NORMAL = "normal"
INTAKE_MODE_DEVELOPER = "developer"
REPO_OPTION_NONE = "__NONE__"
REPO_OPTION_NEW = "__NEW__"
REPO_OPTION_CONNECT = "__CONNECT__"
BRANCH_OPTION_NONE = "__NONE__"
BRANCH_OPTION_NEW = "__NEW__"
GITHUB_OPTION_CACHE_TTL_SECONDS = 120


@dataclass
class IntakeSession:
    mode: str  # create | update
    feature_id: str
    user_id: str
    team_id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    queue: list[str] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)
    asked_fields: set[str] = field(default_factory=set)
    base_spec: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


ACTIVE_INTAKES: dict[str, IntakeSession] = {}
APP_HOME_WELCOME_CACHE: dict[str, float] = {}
GITHUB_REPO_OPTIONS_CACHE: dict[str, tuple[float, list[str]]] = {}
GITHUB_BRANCH_OPTIONS_CACHE: dict[str, tuple[float, list[str]]] = {}


def _parse_lines(text: str) -> list[str]:
    return [line.strip().lstrip("- ").strip() for line in (text or "").splitlines() if line.strip()]


def _dedupe(values: list[str]) -> list[str]:
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


def _extract_urls(text: str) -> list[str]:
    return _dedupe(URL_RE.findall(text or ""))


def _extract_file_links(event: dict[str, Any]) -> list[str]:
    links: list[str] = []
    for item in event.get("files") or []:
        if not isinstance(item, dict):
            continue
        permalink = str(item.get("permalink") or "").strip()
        if permalink:
            links.append(permalink)
    return _dedupe(links)


def _is_skip(text: str) -> bool:
    value = (text or "").strip().lower()
    return value in SKIP_TOKENS


def _normalize_mode(text: str) -> str:
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


def _format_mode(mode: str) -> str:
    if mode == "reuse_existing":
        return "Reuse existing repo patterns"
    return "Build in target repo (default)"


def _normalize_intake_mode(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized == INTAKE_MODE_DEVELOPER:
        return INTAKE_MODE_DEVELOPER
    return INTAKE_MODE_NORMAL


def _session_intake_mode(session: IntakeSession) -> str:
    return _normalize_intake_mode(str((session.answers or {}).get("_intake_mode") or INTAKE_MODE_NORMAL))


def _set_session_intake_mode(session: IntakeSession, mode: str) -> None:
    session.answers["_intake_mode"] = _normalize_intake_mode(mode)


def _intake_mode_label(mode: str) -> str:
    return "Developer" if _normalize_intake_mode(mode) == INTAKE_MODE_DEVELOPER else "Normal"


def _intake_mode_toggle_label(mode: str) -> str:
    current = _normalize_intake_mode(mode)
    if current == INTAKE_MODE_DEVELOPER:
        return "Switch to Normal"
    return "Switch to Developer"


def _intake_controls_blocks(*, mode: str) -> list[dict[str, Any]]:
    current_mode = _normalize_intake_mode(mode)
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "ff_toggle_mode",
                    "text": {"type": "plain_text", "text": _intake_mode_toggle_label(current_mode)},
                    "value": current_mode,
                },
                {
                    "type": "button",
                    "action_id": "ff_show_help",
                    "text": {"type": "plain_text", "text": "Help"},
                    "value": "help",
                },
            ],
        }
    ]


def _title_prompt_blocks(*, mode: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {"type": "plain_text", "text": QUESTION_BY_FIELD["title"]},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Enter what you want to build, then reply in this thread."},
            ],
        },
        *_intake_controls_blocks(mode=mode),
    ]


def _intake_help_text() -> str:
    return (
        "If PRFactory stops responding, refresh Slack scopes/events and reinstall the app "
        "(see `docs/SETUP_SLACK.md`).\n"
        "If repo dropdown is empty, reconnect GitHub via `/prfactory-github`."
    )


def _slugify_ref(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    if not cleaned:
        return "request"
    parts = [part for part in cleaned.split("-") if part][:3]
    return "-".join(parts)[:32] or "request"


def _feature_reference(*, feature_id: str, title: str) -> str:
    fid = (feature_id or "").strip()
    short = fid[:8] if len(fid) >= 8 else fid
    slug = _slugify_ref(title)
    return f"{slug}-{short}" if short else slug


def _welcome_cache_key(*, team_id: str, user_id: str) -> str:
    return f"{team_id}:{user_id}"


def _should_send_app_home_welcome(*, team_id: str, user_id: str) -> bool:
    if not user_id:
        return False

    now = time.time()
    threshold = now - APP_HOME_WELCOME_TTL_SECONDS
    for key, seen_at in list(APP_HOME_WELCOME_CACHE.items()):
        if seen_at < threshold:
            APP_HOME_WELCOME_CACHE.pop(key, None)

    key = _welcome_cache_key(team_id=team_id, user_id=user_id)
    previous = APP_HOME_WELCOME_CACHE.get(key, 0.0)
    if previous and (now - previous) < APP_HOME_WELCOME_TTL_SECONDS:
        return False
    APP_HOME_WELCOME_CACHE[key] = now
    return True


def _session_key(*, team_id: str, channel_id: str, thread_ts: str, user_id: str) -> str:
    return f"{team_id}:{channel_id}:{thread_ts}:{user_id}"


def _cleanup_expired_sessions() -> None:
    now = time.time()
    expired = [k for k, s in ACTIVE_INTAKES.items() if now - s.started_at > SESSION_TTL_SECONDS]
    for key in expired:
        ACTIVE_INTAKES.pop(key, None)
    try:
        with db_session() as db:
            db.execute(delete(SlackIntakeSession).where(SlackIntakeSession.expires_at <= datetime.now(timezone.utc)))
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]slack intake DB cleanup failed: {e}[/yellow]")


def _session_from_record(record: SlackIntakeSession) -> IntakeSession:
    queue = [str(x) for x in (record.queue or []) if str(x).strip()]
    answers = dict(record.answers or {})
    asked_fields = {str(x) for x in (record.asked_fields or []) if str(x).strip()}
    base_spec = dict(record.base_spec or {})
    started_at = (
        float(record.started_at.timestamp())
        if isinstance(record.started_at, datetime)
        else time.time()
    )
    return IntakeSession(
        mode=str(record.mode or "create"),
        feature_id=str(record.feature_id or ""),
        user_id=str(record.user_id or ""),
        team_id=str(getattr(record, "team_id", "") or ""),
        channel_id=str(record.channel_id or ""),
        thread_ts=str(record.thread_ts or ""),
        message_ts=str(record.message_ts or ""),
        queue=queue,
        answers=answers,
        asked_fields=asked_fields,
        base_spec=base_spec,
        started_at=started_at,
    )


def _store_session(session: IntakeSession) -> None:
    _cleanup_expired_sessions()
    key = _session_key(
        team_id=session.team_id,
        channel_id=session.channel_id,
        thread_ts=session.thread_ts,
        user_id=session.user_id,
    )
    ACTIVE_INTAKES[key] = session
    try:
        with db_session() as db:
            row = db.get(SlackIntakeSession, key)
            if not row:
                row = SlackIntakeSession(session_key=key)
                db.add(row)
            now = datetime.now(timezone.utc)
            row.mode = session.mode
            row.feature_id = session.feature_id
            row.user_id = session.user_id
            row.team_id = session.team_id
            row.channel_id = session.channel_id
            row.thread_ts = session.thread_ts
            row.message_ts = session.message_ts
            row.queue = list(session.queue)
            row.answers = dict(session.answers)
            row.asked_fields = sorted(session.asked_fields)
            row.base_spec = dict(session.base_spec)
            row.started_at = datetime.fromtimestamp(session.started_at, tz=timezone.utc)
            row.updated_at = now
            row.expires_at = now + timedelta(seconds=SESSION_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]slack intake DB persistence failed: {e}[/yellow]")


def _get_session(*, team_id: str, channel_id: str, thread_ts: str, user_id: str) -> IntakeSession | None:
    _cleanup_expired_sessions()
    key = _session_key(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
    cached = ACTIVE_INTAKES.get(key)
    if cached:
        return cached
    try:
        with db_session() as db:
            record = db.get(SlackIntakeSession, key)
            if not record:
                return None
            now = datetime.now(timezone.utc)
            expires_at = record.expires_at
            if isinstance(expires_at, datetime) and expires_at <= now:
                db.delete(record)
                return None
            session = _session_from_record(record)
            ACTIVE_INTAKES[key] = session
            return session
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]slack intake DB read failed: {e}[/yellow]")
        return None


def _drop_session(session: IntakeSession) -> None:
    key = _session_key(
        team_id=session.team_id,
        channel_id=session.channel_id,
        thread_ts=session.thread_ts,
        user_id=session.user_id,
    )
    ACTIVE_INTAKES.pop(key, None)
    try:
        with db_session() as db:
            row = db.get(SlackIntakeSession, key)
            if row:
                db.delete(row)
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]slack intake DB delete failed: {e}[/yellow]")


def _feature_message_blocks(feature: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    fid = feature["id"]
    status = feature["status"]
    title = feature["title"]
    ref = _feature_reference(feature_id=fid, title=title)
    spec = feature.get("spec") or {}
    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    mode_label = _format_mode(mode)
    preview = feature.get("preview_url") or ""
    pr = feature.get("github_pr_url") or ""
    repo_hint = str(spec.get("repo") or "").strip()
    validation = spec.get("_validation") or {}
    missing = validation.get("missing") or []
    missing_summary = ", ".join(missing) if missing else "none"

    actions: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Open dashboard"},
            "url": f"{base_url}/features/{fid}",
        },
        {
            "type": "button",
            "action_id": "ff_add_details",
            "text": {"type": "plain_text", "text": "Add details in chat"},
            "value": fid,
        },
    ]
    if status == "READY_FOR_BUILD":
        actions.append(
            {
                "type": "button",
                "action_id": "ff_run_build",
                "text": {"type": "plain_text", "text": "Run build"},
                "style": "primary",
                "value": fid,
            }
        )
    if status == "PREVIEW_READY":
        actions.append(
            {
                "type": "button",
                "action_id": "ff_approve",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "danger",
                "value": fid,
            }
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{title}*\nStatus: `{status}`\nMode: `{mode}` ({mode_label})\nRef: `{ref}`\nID: `{fid}`\n"
                    f"Missing details: `{missing_summary}`"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Repo: {repo_hint or '(none)'} | PR: {pr or '(pending)'} | "
                        f"Preview: {preview or '(none)'}"
                    ),
                }
            ],
        },
        {"type": "actions", "elements": actions},
    ]


def _validation_questions(feature: dict[str, Any]) -> list[str]:
    spec = feature.get("spec") or {}
    validation = spec.get("_validation") or {}
    missing = [str(x) for x in validation.get("missing") or []]
    questions: list[str] = []
    for field in missing:
        questions.append(QUESTION_BY_FIELD.get(field, f"Please provide `{field}`."))
    return questions


def _post_clarification_prompt(client: Any, channel_id: str, thread_ts: str, feature: dict[str, Any]) -> None:
    questions = _validation_questions(feature)
    if not questions:
        return

    prompt = "I still need a few clarifications before build:\n"
    prompt += "\n".join([f"- {q}" for q in questions])
    prompt += "\nClick *Add details in chat* and reply in this thread."
    client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=prompt)


def _github_connect_url_for_user(settings: Any, *, user_id: str, team_id: str = "") -> str:
    normalized_user = (user_id or "").strip()
    normalized_team = (team_id or "").strip()
    if settings.github_user_oauth_enabled() and normalized_user:
        return settings.github_oauth_install_url_for_user(
            slack_user_id=normalized_user,
            slack_team_id=normalized_team,
        )
    return settings.github_app_install_url_resolved()


def _github_user_connected(settings: Any, *, user_id: str, team_id: str) -> bool:
    if not settings.github_user_oauth_enabled():
        return True
    normalized_user = (user_id or "").strip()
    if not normalized_user:
        return False
    return bool(
        resolve_github_user_access_token(
            slack_user_id=normalized_user,
            slack_team_id=(team_id or "").strip(),
        )
    )


def _github_status_line_for_user(settings: Any, *, user_id: str, team_id: str) -> str:
    if settings.github_user_oauth_enabled():
        if _github_user_connected(settings, user_id=user_id, team_id=team_id):
            return "GitHub account status: connected."
        install_url = _github_connect_url_for_user(settings, user_id=user_id, team_id=team_id)
        if install_url:
            return f"GitHub account status: not connected yet. Connect here: {install_url}"
        return "GitHub account status: not connected yet. Use `/prfactory-github` for setup steps."
    install_url = settings.github_app_install_url_resolved()
    if install_url:
        return f"GitHub app install URL: {install_url}"
    return "Use `/prfactory-github` for GitHub setup guidance."


def _cache_key_for_user(*, user_id: str, team_id: str) -> str:
    return f"{(team_id or '').strip()}:{(user_id or '').strip()}"


def _github_api_headers(*, token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _resolve_github_user_token(*, user_id: str, team_id: str) -> str:
    token = resolve_github_user_access_token(slack_user_id=user_id, slack_team_id=team_id)
    if token or not (team_id or "").strip():
        return token
    # Fallback to user-wide token in case team scoping changed.
    return resolve_github_user_access_token(slack_user_id=user_id, slack_team_id="")


def _fetch_repositories_for_user(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    timeout_seconds: float = 2.5,
) -> list[str]:
    if not settings.github_user_oauth_enabled():
        return []
    token = _resolve_github_user_token(user_id=user_id, team_id=team_id)
    if not token:
        return []

    cache_key = _cache_key_for_user(user_id=user_id, team_id=team_id)
    now = time.time()
    cached = GITHUB_REPO_OPTIONS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < GITHUB_OPTION_CACHE_TTL_SECONDS:
        return list(cached[1])

    repos: list[str] = []
    try:
        response = httpx.get(
            f"{settings.github_api_base.rstrip('/')}/user/repos",
            params={
                "per_page": 100,
                "sort": "updated",
                "affiliation": "owner,collaborator,organization_member",
            },
            headers=_github_api_headers(token=token),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                full_name = str(item.get("full_name") or "").strip()
                owner, repo = parse_repo_slug(full_name)
                if owner and repo:
                    repos.append(f"{owner}/{repo}")
    except Exception as e:
        console.print(
            f"[yellow]github_repo_fetch_failed user={user_id} team={team_id} "
            f"url={settings.github_api_base.rstrip('/')}/user/repos error={e}[/yellow]"
        )
        repos = []

    deduped = _dedupe(repos)
    if deduped:
        GITHUB_REPO_OPTIONS_CACHE[cache_key] = (now, deduped)
    else:
        GITHUB_REPO_OPTIONS_CACHE.pop(cache_key, None)
    return deduped


def _fetch_branches_for_repo(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    repo_slug: str,
    timeout_seconds: float = 2.5,
) -> list[str]:
    owner, repo = parse_repo_slug(repo_slug)
    if not owner or not repo or not settings.github_user_oauth_enabled():
        return []

    token = _resolve_github_user_token(user_id=user_id, team_id=team_id)
    if not token:
        return []

    cache_key = f"{_cache_key_for_user(user_id=user_id, team_id=team_id)}:{owner}/{repo}"
    now = time.time()
    cached = GITHUB_BRANCH_OPTIONS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < GITHUB_OPTION_CACHE_TTL_SECONDS:
        return list(cached[1])

    branches: list[str] = []
    try:
        response = httpx.get(
            f"{settings.github_api_base.rstrip('/')}/repos/{owner}/{repo}/branches",
            params={"per_page": 100},
            headers=_github_api_headers(token=token),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    branches.append(name)
    except Exception as e:
        console.print(
            f"[yellow]github_branch_fetch_failed user={user_id} team={team_id} repo={owner}/{repo} "
            f"error={e}[/yellow]"
        )
        branches = []

    deduped = _dedupe(branches)
    if deduped:
        GITHUB_BRANCH_OPTIONS_CACHE[cache_key] = (now, deduped)
    else:
        GITHUB_BRANCH_OPTIONS_CACHE.pop(cache_key, None)
    return deduped


def _slack_option(*, text: str, value: str) -> dict[str, Any]:
    option_text = (text or "").strip() or value
    option_value = (value or "").strip() or option_text
    return {
        "text": {"type": "plain_text", "text": option_text[:75]},
        "value": option_value[:200],
    }


def _fallback_repo_options() -> list[dict[str, Any]]:
    return [
        _slack_option(text="None (use defaults)", value=REPO_OPTION_NONE),
        _slack_option(text="New repo (I will type it)", value=REPO_OPTION_NEW),
    ]


def _fallback_branch_options() -> list[dict[str, Any]]:
    return [
        _slack_option(text="None (use default branch)", value=BRANCH_OPTION_NONE),
        _slack_option(text="Type branch name", value=BRANCH_OPTION_NEW),
    ]


def _warm_repo_cache(settings: Any, *, user_id: str, team_id: str) -> None:
    try:
        _fetch_repositories_for_user(
            settings,
            user_id=user_id,
            team_id=team_id,
            timeout_seconds=8.0,
        )
    except Exception:
        pass


def _warm_branch_cache(settings: Any, *, user_id: str, team_id: str, repo_slug: str) -> None:
    try:
        _fetch_branches_for_repo(
            settings,
            user_id=user_id,
            team_id=team_id,
            repo_slug=repo_slug,
            timeout_seconds=8.0,
        )
    except Exception:
        pass


def _repo_options_for_slack(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    query: str,
) -> list[dict[str, Any]]:
    raw_query = (query or "").strip()
    typed = raw_query.lower()
    options = _fallback_repo_options()
    has_user_token = False
    has_saved_connection = False
    try:
        has_user_token = bool(_resolve_github_user_token(user_id=user_id, team_id=team_id))
    except Exception:
        has_user_token = False
    try:
        has_saved_connection = bool(has_github_user_connection(slack_user_id=user_id, slack_team_id=team_id))
    except Exception:
        has_saved_connection = False

    if raw_query:
        owner, repo = parse_repo_slug(raw_query)
        repo_candidate = f"{owner}/{repo}" if owner and repo else raw_query
        options.append(_slack_option(text=f"Use {repo_candidate}", value=repo_candidate))

    default_repo = ""
    if (settings.github_repo_owner or "").strip() and (settings.github_repo_name or "").strip():
        default_repo = f"{settings.github_repo_owner.strip()}/{settings.github_repo_name.strip()}"
    if default_repo:
        options.append(_slack_option(text=f"Default: {default_repo}", value=default_repo))

    repos = _fetch_repositories_for_user(settings, user_id=user_id, team_id=team_id)
    if not repos and settings.github_user_oauth_enabled() and not has_user_token:
        console.print(
            f"[yellow]slack_repo_options_empty user={user_id} team={team_id} "
            f"has_token=false has_saved_connection={str(has_saved_connection).lower()}[/yellow]"
        )
        if has_saved_connection:
            options.append(_slack_option(text="Reconnect GitHub (refresh token)", value=REPO_OPTION_CONNECT))
        else:
            options.append(_slack_option(text="Connect GitHub to load repos", value=REPO_OPTION_CONNECT))
        return _dedupe_options(options)[:100]
    if not repos and settings.github_user_oauth_enabled() and has_user_token:
        console.print(
            f"[yellow]slack_repo_options_empty user={user_id} team={team_id} "
            "has_token=true has_saved_connection=true[/yellow]"
        )
        options.append(_slack_option(text="No repos returned - type org/repo", value=REPO_OPTION_NEW))
        return _dedupe_options(options)[:100]

    for slug in repos:
        if typed and typed not in slug.lower():
            continue
        options.append(_slack_option(text=slug, value=slug))
        if len(options) >= 100:
            break
    return _dedupe_options(options)[:100]


def _branch_options_for_slack(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    repo_slug: str,
    query: str,
) -> list[dict[str, Any]]:
    raw_query = (query or "").strip()
    typed = raw_query.lower()
    options = _fallback_branch_options()
    if raw_query and re.match(r"^[A-Za-z0-9._/-]+$", raw_query):
        options.append(_slack_option(text=f"Use {raw_query}", value=raw_query))
    branches = _fetch_branches_for_repo(settings, user_id=user_id, team_id=team_id, repo_slug=repo_slug)
    for branch in branches:
        if typed and typed not in branch.lower():
            continue
        options.append(_slack_option(text=branch, value=branch))
        if len(options) >= 100:
            break
    return _dedupe_options(options)[:100]


def _dedupe_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for option in options:
        value = str((option.get("value") or "")).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped


def _developer_mode_repo_blocks(*, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Target repo (optional).",
            },
            "accessory": {
                "type": "static_select",
                "action_id": "ff_repo_select",
                "placeholder": {"type": "plain_text", "text": "Select repo"},
                "options": options[:100],
            },
        }
    ]


def _developer_mode_branch_blocks(*, repo_slug: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repo_text = repo_slug or "(none)"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Base branch for `{repo_text}` (optional).",
            },
            "accessory": {
                "type": "static_select",
                "action_id": "ff_branch_select",
                "placeholder": {"type": "plain_text", "text": "Select branch"},
                "options": options[:100],
            },
        }
    ]


def _intro_message(settings: Any) -> str:
    app_name = (settings.app_display_name or "PRFactory").strip() or "PRFactory"
    slack_install_url = settings.slack_oauth_install_url_resolved()
    github_line = "- connect GitHub when prompted in-thread (or run `/prfactory-github`)"
    if not settings.github_user_oauth_enabled():
        install_url = settings.github_app_install_url_resolved()
        if install_url:
            github_line = f"- connect GitHub: {install_url}"
    workspace_line = (
        f"- install {app_name} in another workspace: {slack_install_url}"
        if settings.slack_oauth_enabled() and slack_install_url
        else ""
    )
    lines = [
        f"Hi! I am {app_name}. Use `{PRIMARY_SLASH_COMMAND} <what you want built>` in this channel and I will:",
        "- collect details in-thread,",
        "- create a tracked feature request,",
        "- and start a build that opens a PR for review.",
        github_line,
    ]
    if workspace_line:
        lines.append(workspace_line)
    lines.append(f"Dashboard: {settings.base_url}")
    return "\n".join(lines)


def _post_intro_messages(
    client: Any,
    settings: Any,
    *,
    channel_id: str,
    inviter_id: str,
    team_id: str,
    logger: Any,
) -> None:
    text = _intro_message(settings)
    try:
        client.chat_postMessage(channel=channel_id, text=text)
    except Exception as e:  # noqa: BLE001
        logger.warning("slack_intro_channel_post_failed channel=%s error=%s", channel_id, e)

    if not inviter_id:
        return

    app_name = (settings.app_display_name or "PRFactory").strip() or "PRFactory"
    slack_install_url = settings.slack_oauth_install_url_resolved()
    github_help_line = _github_status_line_for_user(settings, user_id=inviter_id, team_id=team_id)
    workspace_help_line = (
        f"Install link for additional Slack workspaces: {slack_install_url}"
        if settings.slack_oauth_enabled() and slack_install_url
        else ""
    )
    dm_lines = [
        f"Thanks for adding {app_name}.",
        f"Anyone in that channel can now use `{PRIMARY_SLASH_COMMAND}` or `{LEGACY_SLASH_COMMAND}` to request work.",
        github_help_line,
    ]
    if workspace_help_line:
        dm_lines.append(workspace_help_line)
    dm_text = "\n".join(dm_lines)
    try:
        client.chat_postMessage(channel=inviter_id, text=dm_text)
    except Exception as e:  # noqa: BLE001
        logger.warning("slack_intro_dm_failed user=%s error=%s", inviter_id, e)


def _api_headers(settings: Any, *, actor: str = "slackbot") -> dict[str, str] | None:
    token = (settings.api_auth_token or "").strip()
    if not token:
        return None
    actor_header = (settings.auth_service_actor_header or "").strip() or "X-Feature-Factory-Actor"
    return {
        "X-FF-Token": token,
        actor_header: actor,
    }


def _ensure_bot_in_channel(client: Any, channel_id: str, logger: Any) -> None:
    """Best-effort auto-join for public channels so thread replies are visible."""

    if not channel_id:
        return
    try:
        client.conversations_join(channel=channel_id)
    except Exception as e:  # noqa: BLE001
        text = str(e)
        non_fatal_tokens = [
            "already_in_channel",
            "method_not_supported_for_channel_type",
            "not_in_channel",
            "is_archived",
            "channel_not_found",
        ]
        if any(token in text for token in non_fatal_tokens):
            return
        logger.warning("slack_conversations_join_failed channel=%s error=%s", channel_id, text)


def _fetch_feature(settings: Any, feature_id: str) -> dict[str, Any]:
    headers = _api_headers(settings)
    r = httpx.get(
        f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}",
        timeout=30,
        headers=headers,
    )
    r.raise_for_status()
    return r.json()


def _update_feature_message(client: Any, feature: dict[str, Any], *, channel_id: str, message_ts: str) -> None:
    settings = get_settings()
    blocks = _feature_message_blocks(feature, settings.base_url)
    client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=f"Feature request: *{feature['title']}*",
        blocks=blocks,
    )


def _default_spec() -> dict[str, Any]:
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
        "risk_flags": [],
        "links": [],
        "debug_build": False,
    }


def _build_create_queue(*, has_title: bool, require_repo: bool, minimal: bool = True) -> list[str]:
    queue = list(CREATE_FLOW_FIELDS_MINIMAL if minimal else CREATE_FLOW_FIELDS_FULL)
    if has_title:
        queue.remove("title")
    if not require_repo and "repo" in queue:
        queue.remove("repo")
    if "repo" not in queue and "base_branch" in queue:
        queue.remove("base_branch")
    return queue


def _repo_required_for_slack_intake(settings: Any) -> bool:
    return bool(settings.github_enabled) and not bool(settings.mock_mode)


def _build_update_queue(feature: dict[str, Any]) -> list[str]:
    spec = feature.get("spec") or {}
    validation = spec.get("_validation") or {}
    missing = [str(x).strip() for x in (validation.get("missing") or []) if str(x).strip()]

    ordered_missing: list[str] = []
    for field in ["title", "problem", "business_justification", "acceptance_criteria", "implementation_mode", "source_repos"]:
        if field in missing:
            ordered_missing.append(field)

    if ordered_missing:
        return ordered_missing
    return list(UPDATE_FALLBACK_FIELDS)


def _next_field(session: IntakeSession) -> str:
    while session.queue:
        current = session.queue[0]
        if current == "base_branch":
            repo_value = str(session.answers.get("repo") or session.base_spec.get("repo") or "").strip()
            if _session_intake_mode(session) != INTAKE_MODE_DEVELOPER or not repo_value:
                session.queue.pop(0)
                continue
        if current == "source_repos":
            mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
            if mode != "reuse_existing":
                session.queue.pop(0)
                continue
        return current
    return ""


def _repo_selection_mutable(session: IntakeSession) -> bool:
    current = _next_field(session)
    if current == "repo":
        return True
    if current == "base_branch" and session.mode == "create" and _session_intake_mode(session) == INTAKE_MODE_DEVELOPER:
        return True
    return False


def _ask_next_question(client: Any, session: IntakeSession) -> None:
    field = _next_field(session)
    if not field:
        return
    if session.mode == "create" and _session_intake_mode(session) == INTAKE_MODE_DEVELOPER:
        if field == "repo":
            settings = get_settings()
            _warm_repo_cache(settings=settings, user_id=session.user_id, team_id=session.team_id)
            options = _repo_options_for_slack(
                settings,
                user_id=session.user_id,
                team_id=session.team_id,
                query="",
            )
            text = "Select a repo, or type `org/repo`."
            existing_ts = str(session.answers.get("_repo_prompt_ts") or "").strip()
            if existing_ts:
                try:
                    client.chat_update(
                        channel=session.channel_id,
                        ts=existing_ts,
                        text=text,
                        blocks=_developer_mode_repo_blocks(options=options),
                    )
                    return
                except Exception:
                    pass
            msg = client.chat_postMessage(
                channel=session.channel_id,
                thread_ts=session.thread_ts,
                text=text,
                blocks=_developer_mode_repo_blocks(options=options),
            )
            session.answers["_repo_prompt_ts"] = str(msg.get("ts") or "").strip()
            _store_session(session)
            return
        if field == "base_branch":
            repo_slug = str(session.answers.get("repo") or "").strip()
            settings = get_settings()
            _warm_branch_cache(
                settings=settings,
                user_id=session.user_id,
                team_id=session.team_id,
                repo_slug=repo_slug,
            )
            options = _branch_options_for_slack(
                settings,
                user_id=session.user_id,
                team_id=session.team_id,
                repo_slug=repo_slug,
                query="",
            )
            text = "Select a base branch, or type one."
            existing_ts = str(session.answers.get("_branch_prompt_ts") or "").strip()
            if existing_ts:
                try:
                    client.chat_update(
                        channel=session.channel_id,
                        ts=existing_ts,
                        text=text,
                        blocks=_developer_mode_branch_blocks(repo_slug=repo_slug, options=options),
                    )
                    return
                except Exception:
                    pass
            msg = client.chat_postMessage(
                channel=session.channel_id,
                thread_ts=session.thread_ts,
                text=text,
                blocks=_developer_mode_branch_blocks(repo_slug=repo_slug, options=options),
            )
            session.answers["_branch_prompt_ts"] = str(msg.get("ts") or "").strip()
            _store_session(session)
            return
    prompt = QUESTION_BY_FIELD.get(field, f"Please provide `{field}`.")
    if session.mode == "update":
        prompt = f"Update request: {prompt}"
    client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=prompt)


def _capture_field_answer(
    session: IntakeSession,
    *,
    field: str,
    event: dict[str, Any],
    require_repo: bool = False,
) -> tuple[bool, str]:
    text = str(event.get("text") or "").strip()
    file_links = _extract_file_links(event)

    if field in {"title", "problem", "business_justification"}:
        if not text or _is_skip(text):
            return False, "That field is required before build. Please provide a short answer."
        session.answers[field] = text
        session.asked_fields.add(field)
        return True, "Captured."

    if field == "links":
        links = _dedupe(_extract_urls(text) + file_links)
        if links:
            existing = [str(x).strip() for x in (session.answers.get("links") or []) if str(x).strip()]
            session.answers["links"] = _dedupe(existing + links)
            session.asked_fields.add("links")
            return True, f"Saved {len(links)} link(s)/attachment(s)."
        return True, "No links added."

    if field == "repo":
        if not text or _is_skip(text):
            if require_repo and _session_intake_mode(session) != INTAKE_MODE_DEVELOPER:
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
        if not text or _is_skip(text):
            session.answers["base_branch"] = ""
            return True, "Using default branch."
        branch = text.splitlines()[0].strip()
        if not re.match(r"^[A-Za-z0-9._/-]+$", branch):
            return False, "Branch name can only include letters, numbers, `.`, `_`, `/`, and `-`."
        session.answers["base_branch"] = branch
        session.asked_fields.add("base_branch")
        return True, f"Base branch set to `{branch}`."

    if field == "implementation_mode":
        mode = _normalize_mode(text)
        if not mode:
            return False, "Please reply with `scratch` or `reuse`."
        session.answers["implementation_mode"] = mode
        session.asked_fields.add("implementation_mode")
        return True, f"Using mode: `{mode}`."

    if field == "source_repos":
        mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
        if mode != "reuse_existing":
            return True, "Reuse references not needed for scratch mode."

        repos = _parse_lines(text)
        if (not repos and _is_skip(text)) or (not repos and not text):
            repo_hint = str(session.answers.get("repo") or session.base_spec.get("repo") or "").strip()
            if repo_hint:
                repos = [repo_hint]
        if not repos:
            return False, "Reuse mode needs at least one reference repo. Please provide one per line."
        session.answers["source_repos"] = repos
        session.asked_fields.add("source_repos")
        return True, f"Saved {len(repos)} source repo reference(s)."

    if field == "proposed_solution":
        if not text or _is_skip(text):
            return True, "No preferred implementation approach captured."
        session.answers["proposed_solution"] = text
        session.asked_fields.add("proposed_solution")
        return True, "Captured implementation notes."

    if field == "acceptance_criteria":
        criteria = _parse_lines(text)
        if (not criteria and _is_skip(text)) or (not criteria and not text):
            session.answers["acceptance_criteria"] = []
            return True, "No explicit acceptance criteria provided. I will use defaults."
        if not criteria:
            return False, "Please provide acceptance criteria lines or reply `skip`."
        session.answers["acceptance_criteria"] = criteria
        session.asked_fields.add("acceptance_criteria")
        return True, f"Captured {len(criteria)} acceptance criteria item(s)."

    return False, f"Unsupported intake field `{field}`."


def _create_spec_from_session(session: IntakeSession) -> dict[str, Any]:
    spec = _default_spec()
    spec.update(session.answers)
    spec.pop("_intake_mode", None)
    spec.pop("_seed_title", None)
    spec.pop("_controls_message_ts", None)
    spec.pop("_repo_prompt_ts", None)
    spec.pop("_branch_prompt_ts", None)

    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    spec["implementation_mode"] = mode
    spec["repo"] = str(spec.get("repo") or "").strip()
    spec["base_branch"] = str(spec.get("base_branch") or "").strip()
    spec["source_repos"] = [str(x).strip() for x in (spec.get("source_repos") or []) if str(x).strip()]
    spec["links"] = [str(x).strip() for x in (spec.get("links") or []) if str(x).strip()]
    if not str(spec.get("problem") or "").strip():
        title = str(spec.get("title") or "").strip()
        spec["problem"] = title if title else "Requested via Slack intake."
    if not str(spec.get("business_justification") or "").strip():
        problem = str(spec.get("problem") or "").strip()
        spec["business_justification"] = (
            f"Requested via Slack intake. Context: {problem[:200]}"
            if problem
            else "Requested via Slack intake."
        )
    criteria = [str(x).strip() for x in (spec.get("acceptance_criteria") or []) if str(x).strip()]
    if not criteria:
        title = str(spec.get("title") or "feature").strip()
        criteria = [
            f"`{title}` is implemented and accessible to end users.",
            "Changes are committed and opened as a PR for review.",
        ]
    spec["acceptance_criteria"] = criteria

    if mode == "reuse_existing" and not spec["source_repos"] and spec.get("repo"):
        spec["source_repos"] = [str(spec["repo"]).strip()]

    return spec


def _update_patch_from_session(session: IntakeSession) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field in sorted(session.asked_fields):
        if field in session.answers:
            patch[field] = session.answers[field]
    return patch


def _start_create_intake(
    client: Any,
    settings: Any,
    *,
    team_id: str,
    channel_id: str,
    user_id: str,
    seed_title: str,
) -> None:
    msg = client.chat_postMessage(
        channel=channel_id,
        text=f"Got it - feature request intake started by <@{user_id}>. Reply in this thread.",
    )
    thread_ts = msg["ts"]

    answers: dict[str, Any] = {}
    answers["_intake_mode"] = INTAKE_MODE_NORMAL
    if seed_title:
        answers["_seed_title"] = seed_title[:200]

    require_repo = _repo_required_for_slack_intake(settings)
    session = IntakeSession(
        mode="create",
        feature_id="",
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_ts=thread_ts,
        # Minimal intake is default: prompt + repo only.
        queue=_build_create_queue(
            has_title=False,
            require_repo=require_repo,
            minimal=bool(settings.slack_intake_minimal),
        ),
        answers=answers,
    )

    _store_session(session)

    controls_message = client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=QUESTION_BY_FIELD["title"],
        blocks=_title_prompt_blocks(mode=_session_intake_mode(session)),
    )
    session.answers["_controls_message_ts"] = str(controls_message.get("ts") or "").strip()
    _store_session(session)

    if _next_field(session) != "title":
        _ask_next_question(client, session)


def _start_update_intake(
    client: Any,
    settings: Any,
    *,
    feature: dict[str, Any],
    team_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    message_ts: str,
) -> None:
    spec = feature.get("spec") or {}
    queue = _build_update_queue(feature)
    answers: dict[str, Any] = {}
    if "links" in queue:
        answers["links"] = [str(x).strip() for x in (spec.get("links") or []) if str(x).strip()]

    session = IntakeSession(
        mode="update",
        feature_id=str(feature.get("id") or ""),
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_ts=message_ts,
        queue=queue,
        answers=answers,
        base_spec=spec,
    )
    _store_session(session)

    client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=(
            f"Updating request `{feature['id']}` in chat. "
            "Reply here and I will update one field at a time. Send `cancel` to stop."
        ),
    )
    _ask_next_question(client, session)


def _enqueue_build_for_feature(
    settings: Any,
    *,
    feature_id: str,
    actor_id: str,
    actor_type: str,
    message: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    headers = _api_headers(settings)
    try:
        r = httpx.post(
            f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}/build",
            json={"actor_type": actor_type, "actor_id": actor_id, "message": message},
            timeout=30,
            headers=headers,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"Failed to contact build endpoint: `{e}`", {"retryable": True}

    if r.status_code >= 400:
        detail_text = ""
        detail_payload: dict[str, Any] = {}
        try:
            payload = r.json()
            detail = payload.get("detail")
            if isinstance(detail, dict):
                missing = [str(x).strip() for x in (detail.get("missing") or []) if str(x).strip()]
                base_message = str(detail.get("message") or "").strip()
                next_action = str(detail.get("next_action") or "").strip()
                install_url = str(detail.get("install_url") or "").strip()
                detail_payload = {
                    "missing": missing,
                    "message": base_message,
                    "next_action": next_action,
                    "install_url": install_url,
                }
                if missing:
                    detail_text = f"{base_message} Missing: {', '.join(missing)}."
                else:
                    detail_text = base_message
                if next_action:
                    detail_text = f"{detail_text} {next_action}".strip()
                if install_url:
                    detail_text = f"{detail_text} Install: {install_url}".strip()
            else:
                detail_text = str(detail or "").strip()
        except Exception:  # noqa: BLE001
            detail_text = (r.text or "").strip()
        if not detail_text:
            detail_text = f"HTTP {r.status_code}"
        if detail_payload:
            message_blob = f"{detail_payload.get('message', '')} {detail_text}".lower()
            detail_payload["needs_github_user_oauth"] = "no github user oauth token" in message_blob
        return False, f"Build was not accepted: {detail_text}", detail_payload or {"retryable": True}

    payload = r.json() if r.content else {}
    if bool(payload.get("already_in_progress")):
        job_id = str(payload.get("job_id") or "").strip() or "(pending assignment)"
        status = str(payload.get("status") or "BUILDING")
        return True, f"Build already running. Job: `{job_id}` | Status: `{status}`", payload
    if bool(payload.get("enqueued")):
        job_id = str(payload.get("job_id") or "").strip() or "(pending assignment)"
        status = str(payload.get("status") or "BUILDING")
        return True, f"Build started. Job: `{job_id}` | Status: `{status}`", payload
    return True, "Build request accepted.", payload


def _action_context(body: dict[str, Any]) -> tuple[str, str, str, str, str]:
    team_id = str(body.get("team", {}).get("id") or body.get("team_id") or "").strip()
    container = body.get("container") or {}
    channel_id = str(body.get("channel", {}).get("id") or container.get("channel_id") or "").strip()
    user_id = str(body.get("user", {}).get("id") or body.get("user_id") or "").strip()
    message = body.get("message") or {}
    thread_ts = str(
        message.get("thread_ts")
        or container.get("thread_ts")
        or message.get("ts")
        or container.get("message_ts")
        or ""
    ).strip()
    message_ts = str(message.get("ts") or container.get("message_ts") or "").strip()
    return team_id, channel_id, user_id, thread_ts, message_ts


def _post_build_retry_message(
    client: Any,
    *,
    channel_id: str,
    thread_ts: str,
    feature_id: str,
    text: str,
    install_url: str = "",
) -> None:
    elements: list[dict[str, Any]] = []
    if install_url:
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Connect GitHub"},
                "url": install_url,
            }
        )
    elements.append(
        {
            "type": "button",
            "action_id": "ff_run_build",
            "text": {"type": "plain_text", "text": "Retry build"},
            "style": "primary",
            "value": feature_id,
        }
    )
    client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=text,
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "actions", "elements": elements},
        ],
    )


def _finalize_create_session(client: Any, settings: Any, session: IntakeSession) -> None:
    spec = _create_spec_from_session(session)
    title = str(spec.get("title") or "").strip() or "(untitled feature)"

    payload = {
        "spec": spec,
        "requester_user_id": session.user_id,
        "slack_team_id": session.team_id,
        "slack_channel_id": session.channel_id,
        "slack_thread_ts": session.thread_ts,
        "slack_message_ts": session.message_ts,
    }

    try:
        headers = _api_headers(settings)
        r = httpx.post(
            f"{settings.orchestrator_internal_url}/api/feature-requests",
            json=payload,
            timeout=30,
            headers=headers,
        )
        r.raise_for_status()
        feature = r.json()
    except Exception as e:  # noqa: BLE001
        client.chat_postMessage(
            channel=session.channel_id,
            thread_ts=session.thread_ts,
            text=f"Failed to create request: `{e}`",
        )
        return
    finally:
        _drop_session(session)

    blocks = _feature_message_blocks(feature, settings.base_url)
    client.chat_update(
        channel=session.channel_id,
        ts=session.message_ts,
        text=f"Feature request: *{title}*",
        blocks=blocks,
    )

    client.chat_postMessage(
        channel=session.channel_id,
        thread_ts=session.thread_ts,
        text=(
            f"Created request `{_feature_reference(feature_id=str(feature.get('id') or ''), title=title)}` "
            f"(id: `{feature['id']}`) with status `{feature['status']}`.\n"
            f"Mode: {_format_mode(str(spec.get('implementation_mode', 'new_feature')))}"
        ),
    )
    if feature.get("status") == "NEEDS_INFO":
        _post_clarification_prompt(client, session.channel_id, session.thread_ts, feature)
        return

    if feature.get("status") == "READY_FOR_BUILD":
        ok, note, payload = _enqueue_build_for_feature(
            settings,
            feature_id=str(feature.get("id") or ""),
            actor_id=session.user_id,
            actor_type="slack",
            message="Build auto-started from Slack intake",
        )
        client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=note)
        try:
            refreshed = _fetch_feature(settings, str(feature.get("id") or ""))
            _update_feature_message(
                client,
                refreshed,
                channel_id=session.channel_id,
                message_ts=session.message_ts,
            )
        except Exception:
            pass
        if not ok:
            install_url = str((payload or {}).get("install_url") or "").strip()
            _post_build_retry_message(
                client,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
                feature_id=str(feature.get("id") or ""),
                text="If you fixed auth/settings, click *Retry build*.",
                install_url=install_url,
            )


def _finalize_update_session(client: Any, settings: Any, session: IntakeSession) -> None:
    patch = _update_patch_from_session(session)
    if not patch:
        client.chat_postMessage(
            channel=session.channel_id,
            thread_ts=session.thread_ts,
            text="No updates captured. Request was not changed.",
        )
        _drop_session(session)
        return

    payload = {
        "spec": patch,
        "actor_type": "slack",
        "actor_id": session.user_id,
        "message": "Spec updated from Slack chat intake",
    }

    try:
        headers = _api_headers(settings)
        r = httpx.patch(
            f"{settings.orchestrator_internal_url}/api/feature-requests/{session.feature_id}/spec",
            json=payload,
            timeout=30,
            headers=headers,
        )
        r.raise_for_status()
        feature = r.json()
    except Exception as e:  # noqa: BLE001
        client.chat_postMessage(
            channel=session.channel_id,
            thread_ts=session.thread_ts,
            text=f"Could not save details for `{session.feature_id}`: `{e}`",
        )
        return
    finally:
        _drop_session(session)

    if session.message_ts:
        _update_feature_message(client, feature, channel_id=session.channel_id, message_ts=session.message_ts)

    client.chat_postMessage(
        channel=session.channel_id,
        thread_ts=session.thread_ts,
        text=f"Updated request `{feature['id']}`. Status is now `{feature['status']}`.",
    )
    if feature.get("status") == "NEEDS_INFO":
        _post_clarification_prompt(client, session.channel_id, session.thread_ts, feature)
    elif feature.get("status") == "READY_FOR_BUILD":
        client.chat_postMessage(
            channel=session.channel_id,
            thread_ts=session.thread_ts,
            text="Spec looks complete. Click *Run build* when ready.",
        )


def create_slack_bolt_app(settings: Any):
    from slack_bolt import App

    oauth_runtime = get_slack_oauth_runtime()
    app_kwargs: dict[str, Any] = {"signing_secret": settings.slack_signing_secret or ""}
    if oauth_runtime is not None:
        app_kwargs["oauth_settings"] = oauth_runtime.oauth_settings
        app_kwargs["installation_store"] = oauth_runtime.installation_store
        app_kwargs["installation_store_bot_only"] = True
        if (settings.slack_bot_token or "").strip():
            app_kwargs["token"] = settings.slack_bot_token
    else:
        app_kwargs["token"] = settings.slack_bot_token

    app = App(**app_kwargs)
    def _resolve_bot_user_id(client: Any) -> str:
        try:
            payload = client.auth_test()
            return str(payload.get("user_id") or "").strip()
        except Exception:
            return ""

    @app.event("member_joined_channel")
    def handle_member_joined_channel(event, body, client, logger):
        channel_id = str(event.get("channel") or "").strip()
        joined_user_id = str(event.get("user") or "").strip()
        inviter_id = str(event.get("inviter") or "").strip()
        team_id = str(body.get("team_id") or event.get("team") or "").strip()
        if not channel_id or not joined_user_id:
            return

        bot_user_id = _resolve_bot_user_id(client)
        if not bot_user_id or joined_user_id != bot_user_id:
            return

        _post_intro_messages(
            client,
            settings,
            channel_id=channel_id,
            inviter_id=inviter_id,
            team_id=team_id,
            logger=logger,
        )

    @app.event("app_home_opened")
    def handle_app_home_opened(event, body, client, logger):
        user_id = str(event.get("user") or "").strip()
        team_id = str(body.get("team_id") or event.get("team") or "").strip()
        if not _should_send_app_home_welcome(team_id=team_id, user_id=user_id):
            return

        app_name = (settings.app_display_name or "PRFactory").strip() or "PRFactory"
        github_help_line = _github_status_line_for_user(settings, user_id=user_id, team_id=team_id)
        text = "\n".join(
            [
                f"Welcome to {app_name}.",
                f"To start in a channel: invite me, then run `{PRIMARY_SLASH_COMMAND} <request>`.",
                github_help_line,
            ]
        )
        try:
            client.chat_postMessage(channel=user_id, text=text)
        except Exception as e:  # noqa: BLE001
            logger.warning("slack_app_home_welcome_failed user=%s error=%s", user_id, e)

    def _handle_create_command(ack, body, client, logger) -> None:
        ack()
        team_id = str(body.get("team_id") or "").strip()
        channel_id = body.get("channel_id")
        user_id = body.get("user_id")
        text = (body.get("text") or "").strip()

        allowed_channels = settings.slack_allowed_channel_set()
        allowed_users = settings.slack_allowed_user_set()

        if allowed_channels and channel_id not in allowed_channels:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Not allowed in this channel.")
            return

        if allowed_users and user_id not in allowed_users:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="You are not allowlisted.")
            return

        _ensure_bot_in_channel(client, channel_id, logger)
        _start_create_intake(
            client,
            settings,
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            seed_title=text,
        )

    @app.command(PRIMARY_SLASH_COMMAND)
    def handle_prfactory(ack, body, client, logger):
        _handle_create_command(ack, body, client, logger)

    @app.command(LEGACY_SLASH_COMMAND)
    def handle_feature_alias(ack, body, client, logger):
        _handle_create_command(ack, body, client, logger)

    @app.command(GITHUB_HELP_SLASH_COMMAND)
    def handle_github_setup(ack, body, client):
        ack()
        team_id = str(body.get("team_id") or "").strip()
        channel_id = body.get("channel_id")
        user_id = body.get("user_id")
        app_name = (settings.app_display_name or "PRFactory").strip() or "PRFactory"
        if settings.github_user_oauth_enabled():
            connected = _github_user_connected(settings, user_id=str(user_id or ""), team_id=team_id)
            install_url = _github_connect_url_for_user(settings, user_id=user_id, team_id=team_id)
            if connected:
                text = (
                    f"GitHub is already connected for your Slack user in {app_name}.\n"
                    f"Run `{PRIMARY_SLASH_COMMAND} <request>` to start a build."
                )
            elif install_url:
                text = (
                    f"GitHub connect for {app_name}:\n"
                    f"1. Open {install_url}\n"
                    "2. Authorize access with your own GitHub account\n"
                    f"3. Run `{PRIMARY_SLASH_COMMAND} <request>` again"
                )
            else:
                text = "GitHub user OAuth is enabled, but install URL is unavailable. Check OAuth settings."
        else:
            install_url = settings.github_app_install_url_resolved()
            text = (
                f"GitHub app setup for {app_name}: {install_url}"
                if install_url
                else "GitHub install URL is not configured. Set `GITHUB_APP_SLUG` or `GITHUB_APP_INSTALL_URL`."
            )
        client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)

    @app.options("ff_repo_select")
    def handle_repo_options(ack, body):
        user_id = str(body.get("user", {}).get("id") or body.get("user_id") or "").strip()
        team_id = str(body.get("team", {}).get("id") or body.get("team_id") or "").strip()
        query = str(body.get("value") or "").strip()
        try:
            options = _repo_options_for_slack(
                settings,
                user_id=user_id,
                team_id=team_id,
                query=query,
            )
        except Exception:
            options = _fallback_repo_options()
        ack(options=options)

    @app.options("ff_branch_select")
    def handle_branch_options(ack, body):
        user_id = str(body.get("user", {}).get("id") or body.get("user_id") or "").strip()
        team_id = str(body.get("team", {}).get("id") or body.get("team_id") or "").strip()
        query = str(body.get("value") or "").strip()

        _team_id, channel_id, _user_id, thread_ts, _message_ts = _action_context(body)
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        repo_slug = ""
        if session:
            repo_slug = str(session.answers.get("repo") or "").strip()
        try:
            options = _branch_options_for_slack(
                settings,
                user_id=user_id,
                team_id=team_id,
                repo_slug=repo_slug,
                query=query,
            )
        except Exception:
            options = _fallback_branch_options()
        ack(options=options)

    @app.action("ff_toggle_mode")
    def handle_toggle_mode(ack, body, client):
        ack()
        team_id, channel_id, user_id, thread_ts, message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        current = _session_intake_mode(session)
        next_mode = INTAKE_MODE_DEVELOPER if current == INTAKE_MODE_NORMAL else INTAKE_MODE_NORMAL
        _set_session_intake_mode(session, next_mode)
        _store_session(session)
        if next_mode == INTAKE_MODE_DEVELOPER:
            _warm_repo_cache(settings, user_id=user_id, team_id=team_id)
        controls_ts = str(session.answers.get("_controls_message_ts") or message_ts or "").strip()
        if controls_ts:
            try:
                client.chat_update(
                    channel=channel_id,
                    ts=controls_ts,
                    text=QUESTION_BY_FIELD["title"],
                    blocks=_title_prompt_blocks(mode=next_mode),
                )
            except Exception:
                pass
        if _next_field(session) in {"repo", "base_branch"}:
            _ask_next_question(client, session)

    @app.action("ff_show_help")
    def handle_show_help(ack, body, client):
        ack()
        _team_id, channel_id, user_id, _thread_ts, _message_ts = _action_context(body)
        if not (channel_id and user_id):
            return
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=_intake_help_text(),
        )

    @app.action("ff_repo_select")
    def handle_repo_select(ack, body, client):
        ack()
        action = (body.get("actions") or [{}])[0]
        selected = str((action.get("selected_option") or {}).get("value") or "").strip()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        current_field = _next_field(session)
        if not _repo_selection_mutable(session):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Repo selection is no longer needed in this session.")
            return

        if selected == REPO_OPTION_CONNECT:
            install_url = _github_connect_url_for_user(settings, user_id=user_id, team_id=team_id)
            text = f"Connect GitHub first: {install_url}" if install_url else "GitHub connect link is unavailable."
            client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)
            return
        if selected == REPO_OPTION_NEW:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="Reply in thread with `org/repo` (or repo URL) to set the target repository.",
            )
            return
        if selected == REPO_OPTION_NONE:
            session.answers["repo"] = ""
            session.asked_fields.add("repo")
            if session.queue and session.queue[0] == "repo":
                session.queue.pop(0)
            session.answers.pop("base_branch", None)
            session.asked_fields.discard("base_branch")
            session.answers.pop("_branch_prompt_ts", None)
            _store_session(session)
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text="Repo left unspecified.")
            if _next_field(session):
                _ask_next_question(client, session)
            elif session.mode == "create":
                _finalize_create_session(client, settings, session)
            else:
                _finalize_update_session(client, settings, session)
            return

        owner, repo = parse_repo_slug(selected)
        repo_slug = f"{owner}/{repo}" if owner and repo else selected
        previous_repo = str(session.answers.get("repo") or "").strip()
        session.answers["repo"] = repo_slug
        session.asked_fields.add("repo")
        if session.queue and session.queue[0] == "repo":
            session.queue.pop(0)
        if current_field == "base_branch" and repo_slug != previous_repo:
            session.answers.pop("base_branch", None)
            session.asked_fields.discard("base_branch")
            session.answers.pop("_branch_prompt_ts", None)
        _store_session(session)
        if current_field == "base_branch" and repo_slug != previous_repo:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"Updated repo: `{repo_slug}`. Branch options refreshed.",
            )
        else:
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Captured repo: `{repo_slug}`")
        if _next_field(session):
            _ask_next_question(client, session)
        elif session.mode == "create":
            _finalize_create_session(client, settings, session)
        else:
            _finalize_update_session(client, settings, session)

    @app.action("ff_branch_select")
    def handle_branch_select(ack, body, client):
        ack()
        action = (body.get("actions") or [{}])[0]
        selected = str((action.get("selected_option") or {}).get("value") or "").strip()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        if _next_field(session) != "base_branch":
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Base branch selection is no longer needed.")
            return

        if selected == BRANCH_OPTION_NEW:
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="Reply in thread with an existing base branch name (example: `main` or `develop`).",
            )
            return

        if selected == BRANCH_OPTION_NONE:
            session.answers["base_branch"] = ""
            if session.queue and session.queue[0] == "base_branch":
                session.queue.pop(0)
            _store_session(session)
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text="Using repository default base branch.")
            if _next_field(session):
                _ask_next_question(client, session)
            elif session.mode == "create":
                _finalize_create_session(client, settings, session)
            else:
                _finalize_update_session(client, settings, session)
            return

        if not re.match(r"^[A-Za-z0-9._/-]+$", selected):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Invalid branch name.")
            return
        session.answers["base_branch"] = selected
        session.asked_fields.add("base_branch")
        if session.queue and session.queue[0] == "base_branch":
            session.queue.pop(0)
        _store_session(session)
        client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Base branch set to `{selected}`.")
        if _next_field(session):
            _ask_next_question(client, session)
        elif session.mode == "create":
            _finalize_create_session(client, settings, session)
        else:
            _finalize_update_session(client, settings, session)

    @app.event("message")
    def handle_message_events(event, body, client, logger):
        subtype = event.get("subtype")
        if subtype in {"bot_message", "message_changed", "message_deleted", "channel_join", "channel_leave"}:
            return
        if event.get("bot_id"):
            return

        user_id = str(event.get("user") or "").strip()
        team_id = str(body.get("team_id") or event.get("team") or "").strip()
        channel_id = str(event.get("channel") or "").strip()
        thread_ts = str(event.get("thread_ts") or "").strip()
        text = str(event.get("text") or "").strip()

        if not user_id or not channel_id or not thread_ts:
            return

        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            logger.debug(
                "slack_message_event_no_session team=%s channel=%s thread=%s user=%s subtype=%s",
                team_id,
                channel_id,
                thread_ts,
                user_id,
                subtype,
            )
            return

        logger.info(
            "slack_message_event_matched_session team=%s channel=%s thread=%s user=%s field=%s subtype=%s",
            team_id,
            channel_id,
            thread_ts,
            user_id,
            _next_field(session),
            subtype or "",
        )

        if text.lower() in {"cancel", "stop", "quit"}:
            _drop_session(session)
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"Intake cancelled. Use `{PRIMARY_SLASH_COMMAND}` to start again.",
            )
            return

        field = _next_field(session)
        if not field:
            _drop_session(session)
            return

        require_repo = session.mode == "create" and _repo_required_for_slack_intake(settings)
        ok, note = _capture_field_answer(session, field=field, event=event, require_repo=require_repo)
        if not ok:
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=note)
            _ask_next_question(client, session)
            return

        session.queue.pop(0)
        _store_session(session)

        if note:
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=note)

        if _next_field(session):
            _ask_next_question(client, session)
            return

        if session.mode == "create":
            _finalize_create_session(client, settings, session)
            return
        _finalize_update_session(client, settings, session)

    @app.action("ff_add_details")
    def handle_add_details(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        team_id = str(body.get("team", {}).get("id") or body.get("team_id") or "").strip()
        channel_id = body.get("channel", {}).get("id")
        user_id = body["user"]["id"]
        message_ts = body.get("message", {}).get("ts")
        thread_ts = body.get("message", {}).get("thread_ts") or message_ts

        try:
            feature = _fetch_feature(settings, feature_id)
        except Exception as e:  # noqa: BLE001
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Could not load feature details: `{e}`",
            )
            return

        _start_update_intake(
            client,
            settings,
            feature=feature,
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            message_ts=message_ts,
        )

    @app.action("ff_run_build")
    def handle_run_build(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        user_id = body["user"]["id"]
        channel_id = body.get("channel", {}).get("id")
        thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts")

        try:
            current = _fetch_feature(settings, feature_id)
        except Exception:
            current = {}

        if current.get("status") == "NEEDS_INFO":
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="This request still needs details before build.",
            )
            _post_clarification_prompt(client, channel_id, thread_ts, current)
            return

        ok, note, payload = _enqueue_build_for_feature(
            settings,
            feature_id=feature_id,
            actor_id=user_id,
            actor_type="slack",
            message="Build requested from Slack",
        )
        client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=note)
        if ok:
            try:
                refreshed = _fetch_feature(settings, feature_id)
                message_ts = body.get("message", {}).get("ts")
                if message_ts:
                    _update_feature_message(client, refreshed, channel_id=channel_id, message_ts=message_ts)
            except Exception:
                pass
        else:
            install_url = str((payload or {}).get("install_url") or "").strip()
            _post_build_retry_message(
                client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                feature_id=feature_id,
                text="If you fixed auth/settings, click *Retry build*.",
                install_url=install_url,
            )

    @app.action("ff_approve")
    def handle_approve(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        user_id = body["user"]["id"]
        channel_id = body.get("channel", {}).get("id")
        thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts")
        message_ts = body.get("message", {}).get("ts")

        if not is_approver_allowed(user_id):
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Only configured reviewers/admins can approve this feature.",
            )
            return

        try:
            headers = _api_headers(settings)
            r = httpx.post(
                f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}/approve",
                params={"approver": user_id},
                timeout=30,
                headers=headers,
            )
            r.raise_for_status()
            feature = r.json()
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"Approved by <@{user_id}>. Status now `{feature['status']}`",
            )

            if channel_id and message_ts:
                _update_feature_message(client, feature, channel_id=channel_id, message_ts=message_ts)
        except Exception as e:  # noqa: BLE001
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Failed to approve: `{e}`")

    return app


def main() -> None:
    settings = get_settings()

    if not settings.enable_slack_bot:
        console.print("[yellow]Slack bot is disabled (ENABLE_SLACK_BOT=false). Sleeping...[/yellow]")
        while True:
            time.sleep(3600)

    if settings.slack_mode_normalized() != "socket":
        console.print(
            "[yellow]Slack bot process only runs in socket mode; set SLACK_MODE=socket or run HTTP mode in FastAPI.[/yellow]"
        )
        while True:
            time.sleep(3600)

    if not settings.slack_bot_token or not settings.slack_app_token:
        console.print("[red]Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN. Sleeping...[/red]")
        while True:
            time.sleep(3600)

    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = create_slack_bolt_app(settings)
    console.print("[green]Starting Slack Socket Mode handler...[/green]")
    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
