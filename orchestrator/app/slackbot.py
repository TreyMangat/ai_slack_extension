from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import db_session
from app.models import FeatureEvent, FeatureRequest, SlackIntakeSession
from app.services.event_logger import log_event
from app.services.github_repo import parse_repo_slug
from app.services.github_user_oauth import (
    has_github_user_connection,
    resolve_github_user_access_token,
    resolve_github_user_login,
)
from app.services.llm_costs import aggregate_llm_costs, build_llm_cost_context_block
from app.services.repo_indexer_adapter import RepoIndexerError, get_repo_indexer_client
from app.services.slack_oauth import get_slack_oauth_runtime
from app.services.reviewer_service import is_approver_allowed

try:
    from app.services.intake_router import IntakeAction, classify_intake_message

    HAS_INTAKE_ROUTER = True
except ImportError:
    IntakeAction = Any
    classify_intake_message = None
    HAS_INTAKE_ROUTER = False

try:
    from app.services.intake_router import escalate_to_frontier

    HAS_ESCALATE = True
except ImportError:
    escalate_to_frontier = None
    HAS_ESCALATE = False

try:
    from app.services.github_connection import check_github_connection

    HAS_GITHUB_CONNECTION_CHECKER = True
except ImportError:
    check_github_connection = None
    HAS_GITHUB_CONNECTION_CHECKER = False

print(f"[PRFACTORY DIAG] slackbot.py loaded. HAS_INTAKE_ROUTER={HAS_INTAKE_ROUTER}, HAS_ESCALATE={HAS_ESCALATE}, HAS_GITHUB_CONNECTION_CHECKER={HAS_GITHUB_CONNECTION_CHECKER}", flush=True)

console = Console()
module_logger = logging.getLogger("feature_factory.slackbot")


class GitHubAuthError(RuntimeError):
    """Raised when a saved GitHub OAuth connection exists but the token is no longer usable."""

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
    "acceptance_criteria": "Optional: acceptance criteria, one per line. Reply `skip` to use defaults.",
}

CREATE_FLOW_FIELDS_MINIMAL = [
    "title",
    "implementation_mode",
    "edit_scope",
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

SESSION_TTL_SECONDS = 2 * 60 * 60
APP_HOME_WELCOME_TTL_SECONDS = 6 * 60 * 60
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
SKIP_TOKENS = {"skip", "n/a", "na", "none", "no", "not sure", "unsure", "unknown", "idk"}
PRIMARY_SLASH_COMMAND = "/prfactory"
LEGACY_SLASH_COMMAND = "/feature"
GITHUB_HELP_SLASH_COMMAND = "/prfactory-github"
INDEXER_SLASH_COMMAND = "/prfactory-indexer"
INTAKE_MODE_NORMAL = "normal"
INTAKE_MODE_DEVELOPER = "developer"
REPO_OPTION_NONE = "__NONE__"
REPO_OPTION_NEW = "__NEW__"
REPO_OPTION_CONNECT = "__CONNECT__"
BRANCH_OPTION_NONE = "__NONE__"
BRANCH_OPTION_NEW = "__NEW__"
BRANCH_OPTION_AUTOGEN = "__AUTOGEN__"
GITHUB_OPTION_CACHE_TTL_SECONDS = 120
STABLE_BASE_BRANCH_CANDIDATES = ("main", "master", "develop", "dev", "trunk")
BRANCH_WORKTREE_FETCH_TIMEOUT_SECONDS = 8
BRANCH_WORKTREE_CATALOG_ROOT = Path(tempfile.gettempdir()) / "prfactory_branch_catalog"
BRANCH_WORKTREE_PATH_NAME = "selection"
CALLBACK_STALE_ALERTS_DISABLED_EVENT = "callback_stale_alerts_disabled"
OPENROUTER_MINI_MODEL_DEFAULT = "qwen/qwen3.5-9b"
OPENROUTER_FRONTIER_MODEL_DEFAULT = "anthropic/claude-opus-4-6"
AFFIRMATION_PHRASES = {
    "yes", "yeah", "yep", "yup", "correct", "right", "that's right",
    "thats right", "that one", "the right one", "yes thats right",
    "yes that's right", "ya", "sure", "ok", "okay", "confirmed",
}


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
GITHUB_REPO_DEFAULT_BRANCH_CACHE: dict[str, tuple[float, str]] = {}
INDEXER_CATALOG_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _autogenerated_branch_prefixes(settings: Any) -> tuple[str, ...]:
    prefixes: list[str] = ["prfactory/"]
    configured = str(getattr(settings, "llm_push_branch_prefix", "") or "").strip().strip("/").lower()
    if configured:
        prefixes.append(f"{configured}/")
    unique: list[str] = []
    for item in prefixes:
        value = str(item or "").strip().lower()
        if not value:
            continue
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def _is_autogenerated_branch(settings: Any, branch_name: str) -> bool:
    normalized = str(branch_name or "").strip().lower()
    if not normalized:
        return False
    return any(normalized.startswith(prefix) for prefix in _autogenerated_branch_prefixes(settings))


def _stable_branch_fallback(
    settings: Any,
    *,
    branches: list[str],
    default_branch: str = "",
) -> str:
    normalized_default = str(default_branch or "").strip().lower()
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in branches:
        branch = str(item or "").strip()
        if not branch:
            continue
        key = branch.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(branch)
    if normalized_default and not _is_autogenerated_branch(settings, normalized_default):
        return default_branch.strip()
    lower_to_actual = {item.lower(): item for item in cleaned}
    for candidate in STABLE_BASE_BRANCH_CANDIDATES:
        match = lower_to_actual.get(candidate)
        if match and not _is_autogenerated_branch(settings, match):
            return match
    for branch in cleaned:
        lowered = branch.lower()
        if lowered == normalized_default:
            continue
        if not _is_autogenerated_branch(settings, branch):
            return branch
    return ""


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


def _is_stop_command(text: str) -> bool:
    token = re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())
    return token == "stop"


def _disable_stale_alerts_for_thread(*, team_id: str, channel_id: str, thread_ts: str, user_id: str) -> str:
    channel = (channel_id or "").strip()
    thread = (thread_ts or "").strip()
    team = (team_id or "").strip()
    if not channel or not thread:
        return ""

    with db_session() as db:
        stmt = (
            select(FeatureRequest)
            .where(FeatureRequest.slack_channel_id == channel)
            .where(FeatureRequest.slack_thread_ts == thread)
            .order_by(FeatureRequest.updated_at.desc())
            .limit(1)
        )
        if team:
            stmt = stmt.where(
                (FeatureRequest.slack_team_id == team)
                | (FeatureRequest.slack_team_id == "")
            )
        feature = db.execute(stmt).scalars().first()
        if not feature:
            return ""

        disabled_count = (
            db.execute(
                select(func.count())
                .select_from(FeatureEvent)
                .where(FeatureEvent.feature_id == feature.id)
                .where(FeatureEvent.event_type == CALLBACK_STALE_ALERTS_DISABLED_EVENT)
            )
            .scalar_one()
        )
        if int(disabled_count or 0) <= 0:
            log_event(
                db,
                feature,
                event_type=CALLBACK_STALE_ALERTS_DISABLED_EVENT,
                actor_type="slack",
                actor_id=(user_id or "").strip(),
                message="Stale callback reminders disabled from Slack thread.",
                data={
                    "channel_id": channel,
                    "thread_ts": thread,
                    "team_id": team,
                },
            )
        return str(feature.id or "")


def _normalize_branch_name(text: str) -> str:
    branch = str(text or "").splitlines()[0].strip()
    branch = branch.strip("`").strip()
    if branch.lower().startswith("refs/heads/"):
        branch = branch[11:].strip()
    return branch


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


def _title_prompt_blocks(
    *,
    mode: str,
    seed_prompt: str = "",
    github_status_block: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized_seed = str(seed_prompt or "").strip()
    if normalized_seed:
        preview = normalized_seed[:180]
        blocks = [
            {
                "type": "section",
                "text": {"type": "plain_text", "text": "What should this request be titled?"},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Captured prompt: `{preview}`"},
                    {"type": "mrkdwn", "text": "Reply with a short title in this thread."},
                ],
            },
        ]
        if github_status_block is not None:
            blocks.append(github_status_block)
        return [*blocks, *_intake_controls_blocks(mode=mode)]
    blocks = [
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
    ]
    if github_status_block is not None:
        blocks.append(github_status_block)
    return [*blocks, *_intake_controls_blocks(mode=mode)]


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


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_elapsed(seconds: int) -> str:
    total = max(int(seconds), 0)
    minutes, secs = divmod(total, 60)
    hours, mins = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _build_progress_text(feature: dict[str, Any]) -> str:
    status = str(feature.get("status") or "").strip()
    if status != "BUILDING":
        return ""

    runs = feature.get("runs") or []
    active_job_id = str(feature.get("active_build_job_id") or "").strip()
    candidate_run: dict[str, Any] | None = None
    if active_job_id:
        for run in runs:
            if not isinstance(run, dict):
                continue
            if str(run.get("runner_run_id") or "").strip() == active_job_id:
                candidate_run = run
                break
    if candidate_run is None:
        for run in reversed(runs):
            if not isinstance(run, dict):
                continue
            if str(run.get("status") or "").strip().upper() in {"RUNNING", "QUEUED"}:
                candidate_run = run
                break

    now = datetime.now(timezone.utc)
    started_at = _parse_iso_datetime((candidate_run or {}).get("started_at")) or _parse_iso_datetime(feature.get("updated_at"))
    if not started_at:
        started_at = _parse_iso_datetime(feature.get("created_at")) or now
    elapsed_text = _format_elapsed(int((now - started_at).total_seconds()))

    last_signal = _parse_iso_datetime((candidate_run or {}).get("updated_at"))
    events = feature.get("events") or []
    if not last_signal and isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            maybe = _parse_iso_datetime(event.get("created_at"))
            if maybe:
                last_signal = maybe
                break
    signal_text = ""
    if last_signal:
        signal_text = f" | Last signal `{_format_elapsed(int((now - last_signal).total_seconds()))}` ago"
    return f"Build runtime: `{elapsed_text}`{signal_text}"


def _openrouter_enabled(settings: Any) -> bool:
    return bool(str(getattr(settings, "openrouter_api_key", "") or "").strip())


def _display_model_name(model_name: str) -> str:
    value = str(model_name or "").strip()
    if not value:
        return ""
    if "/" in value:
        return value.rsplit("/", 1)[-1]
    return value


def _resolved_model_name(settings: Any, *, tier: str, model_name: str = "") -> str:
    explicit = str(model_name or "").strip()
    if explicit:
        return explicit
    if str(tier or "").strip().lower() == "frontier":
        return str(
            getattr(settings, "openrouter_frontier_model", OPENROUTER_FRONTIER_MODEL_DEFAULT)
            or OPENROUTER_FRONTIER_MODEL_DEFAULT
        ).strip()
    return str(
        getattr(settings, "openrouter_mini_model", OPENROUTER_MINI_MODEL_DEFAULT)
        or OPENROUTER_MINI_MODEL_DEFAULT
    ).strip()


def _model_indicator_block(settings: Any, *, tier: str, model_name: str = "") -> dict[str, Any] | None:
    if not _openrouter_enabled(settings):
        return None
    resolved_model = _display_model_name(_resolved_model_name(settings, tier=tier, model_name=model_name))
    if not resolved_model:
        return None
    label = ":rocket: _Analyzed by {model}_" if str(tier or "").strip().lower() == "frontier" else ":zap: _Assisted by {model}_"
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": label.format(model=resolved_model),
            }
        ],
    }


def _post_thread_message_with_optional_model_context(
    client: Any,
    *,
    channel_id: str,
    thread_ts: str,
    text: str,
    settings: Any,
    tier: str = "",
    model_name: str = "",
    blocks: list[dict[str, Any]] | None = None,
) -> Any:
    indicator = _model_indicator_block(settings, tier=tier, model_name=model_name)
    if indicator is None:
        if blocks:
            return client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text, blocks=blocks)
        return client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)

    final_blocks = list(blocks or [])
    if not final_blocks:
        final_blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
    final_blocks.append(indicator)
    return client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text, blocks=final_blocks)


def _thread_blocks_with_cost_summary(text: str, events: list[Any]) -> list[dict[str, Any]] | None:
    summary = aggregate_llm_costs(events)
    context_block = build_llm_cost_context_block(summary)
    if not context_block:
        return None
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        context_block,
    ]


def _build_thread_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        content = str(item.get("text") or "").strip()
        if not content:
            continue
        role = "assistant" if item.get("bot_id") or str(item.get("subtype") or "").strip() == "bot_message" else "user"
        history.append({"role": role, "content": content})
    return history


def _fetch_thread_messages(client: Any, *, channel_id: str, thread_ts: str, logger: Any) -> list[dict[str, Any]]:
    try:
        response = client.conversations_replies(channel=channel_id, ts=thread_ts, limit=50)
    except Exception:
        logger.error("slack_thread_history_fetch_failed channel=%s thread=%s", channel_id, thread_ts, exc_info=True)
        return []
    if not isinstance(response, dict):
        return []
    return [item for item in (response.get("messages") or []) if isinstance(item, dict)]


def _classify_intake_message_sync(
    *,
    message: str,
    conversation_history: list[dict[str, str]],
    current_fields: dict[str, Any],
    slack_user_id: str = "",
) -> IntakeAction:
    if classify_intake_message is None:
        raise RuntimeError("intake router is unavailable")
    kwargs: dict[str, Any] = {
        "message": message,
        "conversation_history": conversation_history,
        "current_fields": current_fields,
    }
    if slack_user_id:
        kwargs["slack_user_id"] = slack_user_id
    try:
        result = classify_intake_message(**kwargs)
    except TypeError:
        if "slack_user_id" not in kwargs:
            raise
        kwargs.pop("slack_user_id", None)
        result = classify_intake_message(**kwargs)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


def _escalate_to_frontier_sync(
    *,
    message: str,
    conversation_history: list[dict[str, str]],
    current_fields: dict[str, Any],
    slack_user_id: str = "",
) -> IntakeAction:
    if escalate_to_frontier is None:
        raise RuntimeError("frontier escalation is unavailable")
    kwargs: dict[str, Any] = {
        "message": message,
        "conversation_history": conversation_history,
        "current_fields": current_fields,
    }
    if slack_user_id:
        kwargs["slack_user_id"] = slack_user_id
    try:
        result = escalate_to_frontier(**kwargs)
    except TypeError:
        if "slack_user_id" not in kwargs:
            raise
        kwargs.pop("slack_user_id", None)
        result = escalate_to_frontier(**kwargs)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


def _normalize_router_field_name(field_name: str) -> str:
    normalized = str(field_name or "").strip().lower()
    if normalized == "branch":
        return "base_branch"
    if normalized == "description":
        return "problem"
    return normalized


def _normalize_user_skill(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"developer", "non_technical"}:
        return normalized
    return "technical"


def _nontechnical_help_block(field_name: str) -> dict[str, Any] | None:
    normalized = _normalize_router_field_name(field_name)
    help_text_by_field = {
        "repo": ":bulb: _A repository is where the code lives. Pick the one that matches your project._",
        "base_branch": ":bulb: _A branch is the starting version of the code. If you're unsure, the default branch is usually best._",
    }
    help_text = help_text_by_field.get(normalized, "")
    if not help_text:
        return None
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": help_text,
            }
        ],
    }


def _remember_model_context(
    session: IntakeSession,
    *,
    user_skill: str,
    model_name: str,
) -> None:
    session.answers["_last_model_user_skill"] = _normalize_user_skill(user_skill)
    if model_name:
        session.answers["_last_model_name"] = model_name


def _stored_model_user_skill(session: IntakeSession) -> str:
    return _normalize_user_skill(str(session.answers.get("_last_model_user_skill") or "technical"))


def _stored_model_name(session: IntakeSession) -> str:
    return str(session.answers.get("_last_model_name") or "").strip()


def _remember_selection_prompt(
    session: IntakeSession,
    *,
    field_name: str,
    question: str,
    user_skill: str,
    model_name: str,
) -> None:
    normalized = _normalize_router_field_name(field_name)
    if normalized == "repo":
        session.answers["_repo_selection_question"] = question
    elif normalized == "base_branch":
        session.answers["_branch_selection_question"] = question
    _remember_model_context(session, user_skill=user_skill, model_name=model_name)


def _post_model_next_question(
    client: Any,
    *,
    session: IntakeSession,
    settings: Any,
    question: str,
    user_skill: str,
    field_name: str = "",
    tier: str = "mini",
    model_name: str = "",
    blocks: list[dict[str, Any]] | None = None,
) -> Any:
    normalized_skill = _normalize_user_skill(user_skill)
    text = str(question or "").strip()
    final_blocks = list(blocks or [])
    if normalized_skill == "non_technical":
        help_block = _nontechnical_help_block(field_name)
        if help_block is not None:
            final_blocks = [help_block, *final_blocks]
    _remember_model_context(session, user_skill=normalized_skill, model_name=model_name)

    if normalized_skill == "developer":
        if final_blocks:
            return client.chat_postMessage(
                channel=session.channel_id,
                thread_ts=session.thread_ts,
                text=text,
                blocks=final_blocks,
            )
        return client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=text)

    if final_blocks:
        return _post_thread_message_with_optional_model_context(
            client,
            channel_id=session.channel_id,
            thread_ts=session.thread_ts,
            text=text,
            settings=settings,
            tier=tier,
            model_name=model_name,
            blocks=final_blocks,
        )

    return _post_thread_message_with_optional_model_context(
        client,
        channel_id=session.channel_id,
        thread_ts=session.thread_ts,
        text=text,
        settings=settings,
        tier=tier,
        model_name=model_name,
    )


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
    progress_text = _build_progress_text(feature)

    actions: list[dict[str, Any]] = [
        {
            "type": "button",
            "action_id": "ff_add_details",
            "text": {"type": "plain_text", "text": "Add more context"},
            "value": fid,
        },
    ]
    if status == "BUILDING":
        actions.append(
            {
                "type": "button",
                "action_id": "ff_refresh_status",
                "text": {"type": "plain_text", "text": "Refresh status"},
                "value": fid,
            }
        )
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
                },
                *(
                    [{"type": "mrkdwn", "text": progress_text}]
                    if progress_text
                    else []
                ),
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
    prompt += "\nClick *Add more context* and reply in this thread."
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


def _build_github_oauth_url(slack_user_id: str, slack_team_id: str = "") -> str:
    """
    Build the GitHub OAuth initiation URL for this Slack user.
    Reuses the existing /prfactory-github connection flow.
    """
    settings = get_settings()
    url = _github_connect_url_for_user(
        settings,
        user_id=(slack_user_id or "").strip(),
        team_id=(slack_team_id or "").strip(),
    )
    if url:
        return url
    base_url = (settings.base_url or "http://localhost:8000").strip().rstrip("/")
    params = {"slack_user_id": (slack_user_id or "").strip()}
    if (slack_team_id or "").strip():
        params["slack_team_id"] = (slack_team_id or "").strip()
    return f"{base_url}/api/github/install?{urlencode(params)}"


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


def _github_connection_snapshot_sync(*, user_id: str, team_id: str) -> dict[str, str]:
    settings = get_settings()
    normalized_user = (user_id or "").strip()
    normalized_team = (team_id or "").strip()
    if not normalized_user:
        return {"status": "not_connected", "username": ""}
    if not settings.github_user_oauth_enabled():
        return {"status": "connected", "username": ""}

    if HAS_GITHUB_CONNECTION_CHECKER and check_github_connection is not None:
        try:
            kwargs: dict[str, Any] = {
                "slack_user_id": normalized_user,
                "slack_team_id": normalized_team,
            }
            try:
                result = check_github_connection(**kwargs)
            except TypeError:
                kwargs.pop("slack_team_id", None)
                result = check_github_connection(**kwargs)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)

            status_value = str(
                getattr(getattr(result, "status", ""), "value", getattr(result, "status", ""))
            ).strip().lower()
            username = str(
                getattr(result, "username", "")
                or getattr(result, "github_login", "")
                or ""
            ).strip()
            if status_value:
                return {"status": status_value, "username": username}
        except Exception:
            module_logger.error(
                "slack_github_connection_check_failed user=%s team=%s",
                normalized_user,
                normalized_team,
                exc_info=True,
            )

    has_connection = False
    try:
        has_connection = bool(has_github_user_connection(slack_user_id=normalized_user, slack_team_id=normalized_team))
    except Exception:
        has_connection = False

    username = resolve_github_user_login(slack_user_id=normalized_user, slack_team_id=normalized_team)
    token = _resolve_github_user_token(user_id=normalized_user, team_id=normalized_team)
    if token:
        return {"status": "connected", "username": username}
    if has_connection:
        return {"status": "expired", "username": username}
    return {"status": "not_connected", "username": username}


def _github_connection_context_block(*, user_id: str, team_id: str) -> dict[str, Any] | None:
    snapshot = _github_connection_snapshot_sync(user_id=user_id, team_id=team_id)
    status = str(snapshot.get("status") or "").strip().lower()
    username = str(snapshot.get("username") or "").strip()
    if status == "connected":
        label = f":white_check_mark: _GitHub connected as @{username}_" if username else ":white_check_mark: _GitHub connected_"
    elif status == "expired":
        label = ":warning: _GitHub token expired - you'll be prompted to reconnect_"
    else:
        return None
    return {
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": label,
            }
        ],
    }


def _github_prompt_blocks(
    *,
    user_id: str,
    team_id: str,
    mode: str,
) -> list[dict[str, Any]]:
    oauth_url = _build_github_oauth_url(user_id, team_id)
    if mode == "reauth":
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":warning: *Your GitHub connection has expired.*\n"
                        "This happens periodically. Click below to reconnect - it takes about 10 seconds."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reconnect GitHub"},
                        "action_id": "ff_github_reauth",
                        "style": "primary",
                        "url": oauth_url,
                    }
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            ":hourglass_flowing_sand: _I'll continue collecting your request details. "
                            "Once you reconnect, I'll show your repos._"
                        ),
                    }
                ],
            },
        ]
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":link: *Let's connect your GitHub account.*\n"
                    "This lets me show your real repos and branches, and create PRs in the right place."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Connect GitHub"},
                    "action_id": "ff_github_connect",
                    "style": "primary",
                    "url": oauth_url,
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": ":bulb: _You can also type a repo name manually if you prefer._",
                }
            ],
        },
    ]


def _set_waiting_for_github(session: IntakeSession, *, enabled: bool, mode: str = "") -> None:
    if enabled:
        session.answers["_waiting_for_github"] = True
        session.answers["_waiting_for_github_mode"] = str(mode or "connect").strip()
        return
    session.answers.pop("_waiting_for_github", None)
    session.answers.pop("_waiting_for_github_mode", None)


def _waiting_for_github(session: IntakeSession) -> bool:
    return bool(session.answers.get("_waiting_for_github"))


def _waiting_for_github_mode(session: IntakeSession) -> str:
    value = str(session.answers.get("_waiting_for_github_mode") or "").strip().lower()
    if value in {"reauth", "connect"}:
        return value
    return "connect"


def _post_github_connection_prompt(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    user_id: str,
    team_id: str,
    mode: str,
) -> None:
    _set_waiting_for_github(session, enabled=True, mode=mode)
    _store_session(session)
    text = "Reconnect GitHub" if mode == "reauth" else "Connect GitHub"
    client.chat_postMessage(
        channel=session.channel_id,
        thread_ts=session.thread_ts,
        text=text,
        blocks=_github_prompt_blocks(user_id=user_id, team_id=team_id, mode=mode),
    )


def _cache_key_for_user(*, user_id: str, team_id: str) -> str:
    return f"{(team_id or '').strip()}:{(user_id or '').strip()}"


def _github_api_headers(*, token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_basic_auth_extraheader(token: str) -> str:
    raw = f"x-access-token:{token}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"AUTHORIZATION: basic {encoded}"


def _run_git_command(*, cmd: list[str], cwd: Path, timeout_seconds: int) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=max(timeout_seconds, 1),
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise RuntimeError(message)
    return str(result.stdout or "").strip()


def _branch_catalog_paths(*, owner: str, repo: str) -> tuple[Path, Path]:
    normalized = f"{owner.strip().lower()}/{repo.strip().lower()}"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    owner_slug = re.sub(r"[^a-z0-9_.-]+", "-", owner.strip().lower()).strip("-") or "owner"
    repo_slug = re.sub(r"[^a-z0-9_.-]+", "-", repo.strip().lower()).strip("-") or "repo"
    root = BRANCH_WORKTREE_CATALOG_ROOT / f"{owner_slug}--{repo_slug}--{digest}"
    return root / "repo", root / BRANCH_WORKTREE_PATH_NAME


def _remote_default_branch_from_git(*, repo_path: Path) -> str:
    try:
        raw = _run_git_command(
            cmd=["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=repo_path,
            timeout_seconds=10,
        )
    except Exception:
        return ""
    marker = "refs/remotes/origin/"
    if raw.startswith(marker):
        return raw[len(marker) :].strip()
    return ""


def _list_worktree_branches(*, repo_path: Path) -> list[str]:
    try:
        raw = _run_git_command(
            cmd=["git", "worktree", "list", "--porcelain"],
            cwd=repo_path,
            timeout_seconds=10,
        )
    except Exception:
        return []

    branches: list[str] = []
    seen: set[str] = set()
    marker = "refs/heads/"
    for line in raw.splitlines():
        text = str(line or "").strip()
        if not text.startswith("branch "):
            continue
        value = text[len("branch ") :].strip()
        if value.startswith(marker):
            value = value[len(marker) :].strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        branches.append(value)
    return branches


def _list_remote_branches_sorted_by_recent_commit(*, repo_path: Path) -> list[str]:
    try:
        raw = _run_git_command(
            cmd=[
                "git",
                "for-each-ref",
                "refs/remotes/origin",
                "--sort=-committerdate",
                "--format=%(refname:short)",
            ],
            cwd=repo_path,
            timeout_seconds=20,
        )
    except Exception:
        return []

    branches: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        value = str(line or "").strip()
        if not value.startswith("origin/"):
            continue
        name = value[len("origin/") :].strip()
        if not name or name == "HEAD":
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        branches.append(name)
    return branches


def _sort_branch_names_for_selection(
    settings: Any,
    *,
    branches: list[str],
    default_branch: str = "",
    worktree_branches: list[str] | None = None,
) -> list[str]:
    normalized_default = str(default_branch or "").strip().lower()
    worktree_set = {str(x or "").strip().lower() for x in (worktree_branches or []) if str(x or "").strip()}
    candidate_rank = {name: idx for idx, name in enumerate(STABLE_BASE_BRANCH_CANDIDATES)}

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in branches:
        branch = str(item or "").strip()
        if not branch:
            continue
        key = branch.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(branch)

    indexed = list(enumerate(cleaned))

    def _bucket(branch_name: str) -> tuple[int, int]:
        lowered = branch_name.lower()
        if lowered and lowered == normalized_default:
            return (0, 0)
        is_worktree = lowered in worktree_set
        is_generated = _is_autogenerated_branch(settings, branch_name)
        stable_idx = candidate_rank.get(lowered)
        if is_worktree and not is_generated:
            return (1, 0)
        if stable_idx is not None and not is_generated:
            return (2, stable_idx)
        if not is_generated:
            return (3, 0)
        if is_worktree:
            return (4, 0)
        return (5, 0)

    indexed.sort(key=lambda pair: (*_bucket(pair[1]), pair[0], pair[1].lower()))
    return [item[1] for item in indexed]


def _fetch_branches_via_worktree_catalog(
    settings: Any,
    *,
    owner: str,
    repo: str,
    token: str,
    timeout_seconds: float,
) -> tuple[str, list[str]]:
    if shutil.which("git") is None:
        return "", []

    repo_path, selection_worktree = _branch_catalog_paths(owner=owner, repo=repo)
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://github.com/{owner}/{repo}.git"
    auth_header = _github_basic_auth_extraheader(token)
    timeout = max(int(timeout_seconds), 2)

    try:
        if not (repo_path / ".git").exists():
            if repo_path.exists():
                shutil.rmtree(repo_path, ignore_errors=True)
            _run_git_command(
                cmd=[
                    "git",
                    "-c",
                    f"http.https://github.com/.extraheader={auth_header}",
                    "clone",
                    "--filter=blob:none",
                    "--depth",
                    "1",
                    "--no-single-branch",
                    "--no-checkout",
                    clone_url,
                    str(repo_path),
                ],
                cwd=repo_path.parent,
                timeout_seconds=timeout,
            )
        else:
            _run_git_command(
                cmd=[
                    "git",
                    "-c",
                    f"http.https://github.com/.extraheader={auth_header}",
                    "fetch",
                    "--prune",
                    "--no-tags",
                    "--depth",
                    "1",
                    "origin",
                    "+refs/heads/*:refs/remotes/origin/*",
                ],
                cwd=repo_path,
                timeout_seconds=timeout,
            )
        try:
            _run_git_command(
                cmd=[
                    "git",
                    "-c",
                    f"http.https://github.com/.extraheader={auth_header}",
                    "remote",
                    "set-head",
                    "origin",
                    "-a",
                ],
                cwd=repo_path,
                timeout_seconds=timeout,
            )
        except Exception:
            pass
    except Exception as e:
        console.print(
            f"[yellow]github_branch_catalog_sync_failed repo={owner}/{repo} error={e}[/yellow]"
        )
        return "", []

    default_branch = _remote_default_branch_from_git(repo_path=repo_path)
    if default_branch:
        try:
            _run_git_command(cmd=["git", "worktree", "prune"], cwd=repo_path, timeout_seconds=10)
        except Exception:
            pass
        # Keep one detached selection worktree per repo so branch selection can use git-worktree state.
        if not selection_worktree.exists():
            try:
                _run_git_command(
                    cmd=[
                        "git",
                        "worktree",
                        "add",
                        "--detach",
                        str(selection_worktree),
                        f"origin/{default_branch}",
                    ],
                    cwd=repo_path,
                    timeout_seconds=20,
                )
            except Exception:
                pass

    raw_branches = _list_remote_branches_sorted_by_recent_commit(repo_path=repo_path)
    worktree_branches = _list_worktree_branches(repo_path=repo_path)
    sorted_branches = _sort_branch_names_for_selection(
        settings,
        branches=raw_branches,
        default_branch=default_branch,
        worktree_branches=worktree_branches,
    )
    return default_branch, sorted_branches


def _resolve_github_user_token(*, user_id: str, team_id: str) -> str:
    token = resolve_github_user_access_token(slack_user_id=user_id, slack_team_id=team_id)
    if token or not (team_id or "").strip():
        return token
    # Fallback to user-wide token in case team scoping changed.
    return resolve_github_user_access_token(slack_user_id=user_id, slack_team_id="")


def _resolve_indexer_actor_id(*, user_id: str, team_id: str) -> str:
    login = resolve_github_user_login(slack_user_id=user_id, slack_team_id=team_id)
    if login:
        return login
    return str(user_id or "").strip()


def _indexer_cache_key(
    *,
    user_id: str,
    team_id: str,
    actor_id: str,
    query: str,
    top_k_repos: int,
    top_k_branches_per_repo: int,
) -> str:
    query_token = str(query or "").strip().lower()
    return (
        f"{_cache_key_for_user(user_id=user_id, team_id=team_id)}:"
        f"{actor_id.strip().lower()}:{query_token}:{int(top_k_repos)}:{int(top_k_branches_per_repo)}"
    )


def _fetch_indexer_catalog_payload(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    query: str,
    top_k_repos: int,
    top_k_branches_per_repo: int,
    timeout_seconds: float = 2.5,
) -> dict[str, Any]:
    if not settings.repo_indexer_enabled():
        return {}
    actor_id = _resolve_indexer_actor_id(user_id=user_id, team_id=team_id)
    if not actor_id:
        return {}

    cache_key = _indexer_cache_key(
        user_id=user_id,
        team_id=team_id,
        actor_id=actor_id,
        query=query,
        top_k_repos=top_k_repos,
        top_k_branches_per_repo=top_k_branches_per_repo,
    )
    now = time.time()
    cached = INDEXER_CATALOG_CACHE.get(cache_key)
    if cached and (now - cached[0]) < GITHUB_OPTION_CACHE_TTL_SECONDS:
        return dict(cached[1] or {})

    indexer = get_repo_indexer_client(settings=settings)
    if indexer is None:
        return {}
    runtime_timeout = max(timeout_seconds, float(getattr(settings, "indexer_timeout_seconds", 0) or 0))
    indexer = indexer.__class__(
        base_url=indexer.base_url,
        auth_token=indexer.auth_token,
        timeout_seconds=max(runtime_timeout, 0.5),
    )

    try:
        payload = indexer.suggest_repos_and_branches(
            actor_id=actor_id,
            query=query,
            top_k_repos=max(int(top_k_repos), 1),
            top_k_branches_per_repo=max(int(top_k_branches_per_repo), 1),
        )
    except RepoIndexerError as e:
        console.print(
            f"[yellow]indexer_catalog_suggest_failed actor={actor_id} query={query!r} error={e}[/yellow]"
        )
        return {}
    except Exception as e:  # noqa: BLE001
        console.print(
            f"[yellow]indexer_catalog_suggest_failed actor={actor_id} query={query!r} error={e}[/yellow]"
        )
        return {}

    if isinstance(payload, dict):
        INDEXER_CATALOG_CACHE[cache_key] = (now, payload)
        return payload
    return {}


def _indexer_repo_slug(repo_payload: dict[str, Any]) -> str:
    full_name = str(repo_payload.get("full_name") or "").strip()
    if full_name:
        owner, repo = parse_repo_slug(full_name)
        if owner and repo:
            return f"{owner}/{repo}"
        return full_name
    owner = str(repo_payload.get("owner") or "").strip()
    name = str(repo_payload.get("name") or "").strip()
    if owner and name:
        return f"{owner}/{name}"
    return str(repo_payload.get("name") or repo_payload.get("id") or "").strip()


def _indexer_repo_slugs(payload: dict[str, Any]) -> list[str]:
    repos: list[str] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        repo_payload = item.get("repo")
        if not isinstance(repo_payload, dict):
            continue
        slug = _indexer_repo_slug(repo_payload)
        if slug:
            repos.append(slug)
    return _dedupe(repos)


def _indexer_repo_suggestion_for_slug(payload: dict[str, Any], *, owner: str, repo: str) -> dict[str, Any]:
    target = f"{owner.strip().lower()}/{repo.strip().lower()}"
    fallback: dict[str, Any] = {}
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        repo_payload = item.get("repo")
        if not isinstance(repo_payload, dict):
            continue
        slug = _indexer_repo_slug(repo_payload).strip().lower()
        if not slug:
            continue
        if slug == target:
            return item
        if not fallback and slug.endswith(f"/{repo.strip().lower()}"):
            fallback = item
    return fallback


def _indexer_branch_names_and_default(
    settings: Any,
    *,
    suggestion: dict[str, Any],
) -> tuple[list[str], str]:
    repo_payload = suggestion.get("repo") if isinstance(suggestion, dict) else {}
    repo_payload = repo_payload if isinstance(repo_payload, dict) else {}
    default_branch = str(repo_payload.get("default_branch") or "").strip()
    branches: list[str] = []
    for item in suggestion.get("branches") or []:
        if not isinstance(item, dict):
            continue
        branch_name = str(item.get("name") or "").strip()
        if not branch_name:
            continue
        branches.append(branch_name)
        if not default_branch and bool(item.get("is_default")):
            default_branch = branch_name
    deduped = _dedupe(branches)
    sorted_branches = _sort_branch_names_for_selection(
        settings,
        branches=deduped,
        default_branch=default_branch,
        worktree_branches=[],
    )
    return sorted_branches, default_branch


def _indexer_connect_url_for_user(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    timeout_seconds: float = 2.5,
) -> str:
    payload = _fetch_indexer_catalog_payload(
        settings,
        user_id=user_id,
        team_id=team_id,
        query="",
        top_k_repos=1,
        top_k_branches_per_repo=1,
        timeout_seconds=timeout_seconds,
    )
    if not payload:
        return ""
    if not bool(payload.get("auth_required")):
        return ""
    return str(payload.get("connect_url") or "").strip()


def _fetch_repositories_for_user(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    timeout_seconds: float = 2.5,
) -> list[str]:
    cache_key = _cache_key_for_user(user_id=user_id, team_id=team_id)
    now = time.time()
    cached = GITHUB_REPO_OPTIONS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < GITHUB_OPTION_CACHE_TTL_SECONDS:
        return list(cached[1])

    if settings.repo_indexer_enabled():
        indexer_payload = _fetch_indexer_catalog_payload(
            settings,
            user_id=user_id,
            team_id=team_id,
            query="",
            top_k_repos=100,
            top_k_branches_per_repo=1,
            timeout_seconds=timeout_seconds,
        )
        indexer_repos = _indexer_repo_slugs(indexer_payload)
        if indexer_repos:
            GITHUB_REPO_OPTIONS_CACHE[cache_key] = (now, indexer_repos)
            return list(indexer_repos)

    if not settings.github_user_oauth_enabled():
        return []
    token = _resolve_github_user_token(user_id=user_id, team_id=team_id)
    if not token:
        return []

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
        if response.status_code == 401:
            raise GitHubAuthError("GitHub OAuth token expired while loading repos.")
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
    except GitHubAuthError:
        raise
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
    if not owner or not repo:
        return []

    cache_key = f"{_cache_key_for_user(user_id=user_id, team_id=team_id)}:{owner}/{repo}"
    now = time.time()
    cached = GITHUB_BRANCH_OPTIONS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < GITHUB_OPTION_CACHE_TTL_SECONDS:
        return list(cached[1])

    if settings.repo_indexer_enabled():
        indexer_payload = _fetch_indexer_catalog_payload(
            settings,
            user_id=user_id,
            team_id=team_id,
            query=f"{owner}/{repo}",
            top_k_repos=20,
            top_k_branches_per_repo=max(int(getattr(settings, "indexer_top_k_branches_per_repo", 8) or 8), 1),
            timeout_seconds=timeout_seconds,
        )
        suggestion = _indexer_repo_suggestion_for_slug(indexer_payload, owner=owner, repo=repo)
        if suggestion:
            branches, default_branch = _indexer_branch_names_and_default(settings, suggestion=suggestion)
            if branches:
                GITHUB_BRANCH_OPTIONS_CACHE[cache_key] = (now, list(branches))
                if default_branch:
                    GITHUB_REPO_DEFAULT_BRANCH_CACHE[cache_key] = (now, default_branch)
                return list(branches)

    if not settings.github_user_oauth_enabled():
        return []
    token = _resolve_github_user_token(user_id=user_id, team_id=team_id)
    if not token:
        return []

    default_from_git, worktree_branches = _fetch_branches_via_worktree_catalog(
        settings,
        owner=owner,
        repo=repo,
        token=token,
        timeout_seconds=max(timeout_seconds, BRANCH_WORKTREE_FETCH_TIMEOUT_SECONDS),
    )
    if worktree_branches:
        GITHUB_BRANCH_OPTIONS_CACHE[cache_key] = (now, list(worktree_branches))
        if default_from_git:
            GITHUB_REPO_DEFAULT_BRANCH_CACHE[cache_key] = (now, default_from_git)
        return list(worktree_branches)

    branches: list[str] = []
    try:
        response = httpx.get(
            f"{settings.github_api_base.rstrip('/')}/repos/{owner}/{repo}/branches",
            params={"per_page": 100},
            headers=_github_api_headers(token=token),
            timeout=timeout_seconds,
        )
        if response.status_code == 401:
            raise GitHubAuthError("GitHub OAuth token expired while loading branches.")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    branches.append(name)
    except GitHubAuthError:
        raise
    except Exception as e:
        console.print(
            f"[yellow]github_branch_fetch_failed user={user_id} team={team_id} repo={owner}/{repo} "
            f"error={e}[/yellow]"
        )
        branches = []

    deduped = _dedupe(branches)
    default_cached = str((GITHUB_REPO_DEFAULT_BRANCH_CACHE.get(cache_key) or (0.0, ""))[1] or "").strip()
    sorted_branches = _sort_branch_names_for_selection(
        settings,
        branches=deduped,
        default_branch=default_cached,
        worktree_branches=[],
    )
    if sorted_branches:
        GITHUB_BRANCH_OPTIONS_CACHE[cache_key] = (now, sorted_branches)
    else:
        GITHUB_BRANCH_OPTIONS_CACHE.pop(cache_key, None)
    return sorted_branches


def _fetch_default_branch_for_repo(
    settings: Any,
    *,
    user_id: str,
    team_id: str,
    repo_slug: str,
    timeout_seconds: float = 2.5,
) -> str:
    owner, repo = parse_repo_slug(repo_slug)
    if not owner or not repo:
        return ""

    cache_key = f"{_cache_key_for_user(user_id=user_id, team_id=team_id)}:{owner}/{repo}"
    now = time.time()
    cached = GITHUB_REPO_DEFAULT_BRANCH_CACHE.get(cache_key)
    if cached and (now - cached[0]) < GITHUB_OPTION_CACHE_TTL_SECONDS:
        return str(cached[1] or "")

    if settings.repo_indexer_enabled():
        indexer_payload = _fetch_indexer_catalog_payload(
            settings,
            user_id=user_id,
            team_id=team_id,
            query=f"{owner}/{repo}",
            top_k_repos=20,
            top_k_branches_per_repo=max(int(getattr(settings, "indexer_top_k_branches_per_repo", 8) or 8), 1),
            timeout_seconds=timeout_seconds,
        )
        suggestion = _indexer_repo_suggestion_for_slug(indexer_payload, owner=owner, repo=repo)
        if suggestion:
            _branches, default_from_indexer = _indexer_branch_names_and_default(settings, suggestion=suggestion)
            if default_from_indexer:
                GITHUB_REPO_DEFAULT_BRANCH_CACHE[cache_key] = (now, default_from_indexer)
                return default_from_indexer

    if not settings.github_user_oauth_enabled():
        return ""
    token = _resolve_github_user_token(user_id=user_id, team_id=team_id)
    if not token:
        return ""

    default_from_git, worktree_branches = _fetch_branches_via_worktree_catalog(
        settings,
        owner=owner,
        repo=repo,
        token=token,
        timeout_seconds=max(timeout_seconds, BRANCH_WORKTREE_FETCH_TIMEOUT_SECONDS),
    )
    if default_from_git:
        GITHUB_REPO_DEFAULT_BRANCH_CACHE[cache_key] = (now, default_from_git)
        if worktree_branches:
            GITHUB_BRANCH_OPTIONS_CACHE[cache_key] = (now, list(worktree_branches))
        return default_from_git

    default_branch = ""
    try:
        response = httpx.get(
            f"{settings.github_api_base.rstrip('/')}/repos/{owner}/{repo}",
            headers=_github_api_headers(token=token),
            timeout=timeout_seconds,
        )
        if response.status_code == 401:
            raise GitHubAuthError("GitHub OAuth token expired while loading default branch.")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            default_branch = str(payload.get("default_branch") or "").strip()
    except GitHubAuthError:
        raise
    except Exception as e:
        console.print(
            f"[yellow]github_default_branch_fetch_failed user={user_id} team={team_id} repo={owner}/{repo} "
            f"error={e}[/yellow]"
        )
        default_branch = ""

    if default_branch:
        GITHUB_REPO_DEFAULT_BRANCH_CACHE[cache_key] = (now, default_branch)
    else:
        GITHUB_REPO_DEFAULT_BRANCH_CACHE.pop(cache_key, None)
    return default_branch


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
        _slack_option(text="Auto-create PRFactory branch (recommended)", value=BRANCH_OPTION_AUTOGEN),
        _slack_option(text="None (use default base branch)", value=BRANCH_OPTION_NONE),
        _slack_option(text="Type existing base branch", value=BRANCH_OPTION_NEW),
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
        _fetch_default_branch_for_repo(
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
    indexer_connect_url = ""
    if not repos and settings.repo_indexer_enabled():
        indexer_connect_url = _indexer_connect_url_for_user(
            settings,
            user_id=user_id,
            team_id=team_id,
        )
    if not repos and settings.github_user_oauth_enabled() and not has_user_token:
        console.print(
            f"[yellow]slack_repo_options_empty user={user_id} team={team_id} "
            f"has_token=false has_saved_connection={str(has_saved_connection).lower()}[/yellow]"
        )
        if has_saved_connection:
            options.append(_slack_option(text="Reconnect GitHub (refresh token)", value=REPO_OPTION_CONNECT))
        elif indexer_connect_url:
            options.append(_slack_option(text="Connect GitHub (PRFactory + indexer)", value=REPO_OPTION_CONNECT))
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
    if not repos and indexer_connect_url:
        options.append(_slack_option(text="Connect GitHub to Repo_Indexer", value=REPO_OPTION_CONNECT))
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
    default_branch = _fetch_default_branch_for_repo(
        settings,
        user_id=user_id,
        team_id=team_id,
        repo_slug=repo_slug,
    )
    fallback = _stable_branch_fallback(settings, branches=branches, default_branch=default_branch)
    if default_branch:
        default_label = f"Default: {default_branch}"
        if _is_autogenerated_branch(settings, default_branch):
            if fallback and fallback.lower() != default_branch.lower():
                default_label = f"{default_label} (bot branch; fallback {fallback})"
            else:
                default_label = f"{default_label} (bot branch)"
        options.append(_slack_option(text=default_label, value=default_branch))
    stable_branches: list[str] = []
    generated_branches: list[str] = []
    for branch in branches:
        if typed and typed not in branch.lower():
            continue
        if _is_autogenerated_branch(settings, branch):
            generated_branches.append(branch)
        else:
            stable_branches.append(branch)
    for branch in stable_branches:
        options.append(_slack_option(text=branch, value=branch))
        if len(options) >= 100:
            return _dedupe_options(options)[:100]
    for branch in generated_branches:
        options.append(_slack_option(text=f"Bot branch: {branch}", value=branch))
        if len(options) >= 100:
            return _dedupe_options(options)[:100]
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


def _initial_option(options: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    for option in options:
        if str(option.get("value") or "").strip() == normalized:
            return option
    return None


def _developer_mode_repo_blocks(
    *,
    options: list[dict[str, Any]],
    prompt_text: str = "Target repo (optional).",
    placeholder_text: str = "Select repo",
    initial_value: str = "",
) -> list[dict[str, Any]]:
    accessory: dict[str, Any] = {
        "type": "static_select",
        "action_id": "ff_repo_select",
        "placeholder": {"type": "plain_text", "text": placeholder_text},
        "options": options[:100],
    }
    initial = _initial_option(options, initial_value)
    if initial is not None:
        accessory["initial_option"] = initial
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": prompt_text,
            },
            "accessory": accessory,
        }
    ]


def _developer_mode_branch_blocks(
    *,
    repo_slug: str,
    options: list[dict[str, Any]],
    prompt_text: str = "",
    placeholder_text: str = "Select branch",
    initial_value: str = "",
) -> list[dict[str, Any]]:
    repo_text = repo_slug or "(none)"
    section_text = prompt_text or f"Base branch for `{repo_text}` (optional)."
    accessory: dict[str, Any] = {
        "type": "static_select",
        "action_id": "ff_branch_select",
        "placeholder": {"type": "plain_text", "text": placeholder_text},
        "options": options[:100],
    }
    initial = _initial_option(options, initial_value)
    if initial is not None:
        accessory["initial_option"] = initial
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": section_text,
            },
            "accessory": accessory,
        }
    ]


def _build_repo_select_blocks(
    settings: Any,
    session: IntakeSession,
    *,
    question: str,
    suggested: str | None,
    user_skill: str,
) -> list[dict[str, Any]]:
    normalized_skill = _normalize_user_skill(user_skill)
    suggested_value = str(suggested or "").strip()
    options = _repo_options_for_slack(
        settings,
        user_id=session.user_id,
        team_id=session.team_id,
        query=suggested_value,
    )
    placeholder_text = "Pick a project" if normalized_skill == "non_technical" else "Select repo"
    blocks = _developer_mode_repo_blocks(
        options=options,
        prompt_text=question or "Which repository should this go in?",
        placeholder_text=placeholder_text,
        initial_value=suggested_value,
    )
    if suggested_value:
        alternate_label = "Pick a project" if normalized_skill == "non_technical" else "Pick a different repo"
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": f"Use {suggested_value}"},
                        "action_id": "ff_accept_repo_suggestion",
                        "value": suggested_value,
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": alternate_label},
                        "action_id": "ff_show_repo_dropdown",
                    },
                ],
            }
        )
    # Hint when very few real repos are listed (GitHub App may be limited)
    _special_values = {REPO_OPTION_NONE, REPO_OPTION_NEW, REPO_OPTION_CONNECT}
    real_repo_count = sum(
        1 for opt in options
        if isinstance(opt, dict) and str(opt.get("value") or "").strip() not in _special_values
    )
    if real_repo_count <= 1:
        install_url = str(getattr(settings, "github_app_install_url_resolved", lambda: "")() or "").strip()
        hint_url = install_url or "https://github.com/settings/installations"
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    ":information_source: _Only showing repos where the GitHub App is installed. "
                    f"<{hint_url}|Add more repos>_"
                ),
            }],
        })
    return blocks


def _build_branch_select_blocks(
    settings: Any,
    session: IntakeSession,
    *,
    question: str,
    suggested: str | None,
    user_skill: str,
) -> list[dict[str, Any]]:
    normalized_skill = _normalize_user_skill(user_skill)
    repo_slug = str(session.answers.get("repo") or session.base_spec.get("repo") or "").strip()
    suggested_value = str(suggested or "").strip()
    options = _branch_options_for_slack(
        settings,
        user_id=session.user_id,
        team_id=session.team_id,
        repo_slug=repo_slug,
        query=suggested_value,
    )
    placeholder_text = "Pick a branch" if normalized_skill == "non_technical" else "Select branch"
    blocks = _developer_mode_branch_blocks(
        repo_slug=repo_slug,
        options=options,
        prompt_text=question or f"Which branch should we use for `{repo_slug or 'this repo'}`?",
        placeholder_text=placeholder_text,
        initial_value=suggested_value,
    )
    if suggested_value:
        alternate_label = "Pick a branch" if normalized_skill == "non_technical" else "Pick a different branch"
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": f"Use {suggested_value}"},
                        "action_id": "ff_accept_branch_suggestion",
                        "value": suggested_value,
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": alternate_label},
                        "action_id": "ff_show_branch_dropdown",
                    },
                ],
            }
        )
    return blocks


def _show_repo_dropdown_message(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    question: str,
    suggested: str | None = None,
    user_skill: str = "technical",
    model_name: str = "",
) -> None:
    normalized_skill = _normalize_user_skill(user_skill)
    prompt_text = question or "Which repository should this go in?"
    try:
        blocks = _build_repo_select_blocks(
            settings,
            session,
            question=prompt_text,
            suggested=suggested,
            user_skill=normalized_skill,
        )
    except GitHubAuthError:
        _post_github_connection_prompt(
            client,
            settings,
            session,
            user_id=session.user_id,
            team_id=session.team_id,
            mode="reauth",
        )
        return
    _remember_selection_prompt(
        session,
        field_name="repo",
        question=prompt_text,
        user_skill=normalized_skill,
        model_name=model_name,
    )
    if suggested:
        session.answers["_suggested_repo"] = str(suggested).strip()
    _store_session(session)
    _post_model_next_question(
        client,
        session=session,
        settings=settings,
        question=prompt_text,
        user_skill=normalized_skill,
        field_name="repo",
        tier="mini",
        model_name=model_name,
        blocks=blocks,
    )


def _show_branch_dropdown_message(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    question: str,
    suggested: str | None = None,
    user_skill: str = "technical",
    model_name: str = "",
) -> None:
    repo_slug = str(session.answers.get("repo") or session.base_spec.get("repo") or "").strip()
    if not repo_slug:
        _show_repo_dropdown_message(
            client,
            settings,
            session,
            question="Pick the repository first, then choose a branch.",
            suggested=None,
            user_skill=user_skill,
            model_name=model_name,
        )
        return
    _set_session_intake_mode(session, INTAKE_MODE_DEVELOPER)
    normalized_skill = _normalize_user_skill(user_skill)
    prompt_text = question or f"Which branch should we use for `{repo_slug}`?"
    try:
        blocks = _build_branch_select_blocks(
            settings,
            session,
            question=prompt_text,
            suggested=suggested,
            user_skill=normalized_skill,
        )
    except GitHubAuthError:
        _post_github_connection_prompt(
            client,
            settings,
            session,
            user_id=session.user_id,
            team_id=session.team_id,
            mode="reauth",
        )
        return
    _remember_selection_prompt(
        session,
        field_name="base_branch",
        question=prompt_text,
        user_skill=normalized_skill,
        model_name=model_name,
    )
    _store_session(session)
    _post_model_next_question(
        client,
        session=session,
        settings=settings,
        question=prompt_text,
        user_skill=normalized_skill,
        field_name="base_branch",
        tier="mini",
        model_name=model_name,
        blocks=blocks,
    )


def _advance_to_next_field(
    client: Any,
    settings: Any,
    session: IntakeSession,
) -> None:
    """After a field is confirmed, move to the next missing field or finalize."""
    next_field = _peek_next_field(session)
    if next_field == "base_branch":
        _show_branch_dropdown_message(
            client,
            settings,
            session,
            question="Which branch should we build from?",
            suggested=None,
            user_skill=_stored_model_user_skill(session),
            model_name=_stored_model_name(session),
        )
    elif next_field:
        _ask_next_question(client, session)
    else:
        _finalize_session(client, settings, session)


def _truncate_inline_text(text: str, *, limit: int = 160) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 3, 1)].rstrip() + "..."


def _indexer_search_repo_label(repo_payload: dict[str, Any]) -> str:
    slug = _indexer_repo_slug(repo_payload)
    if slug:
        return slug
    source_ref = str(repo_payload.get("source_ref") or "").strip()
    if source_ref:
        owner, repo = parse_repo_slug(source_ref)
        if owner and repo:
            return f"{owner}/{repo}"
        return source_ref
    return str(repo_payload.get("name") or repo_payload.get("id") or "(unknown repo)").strip() or "(unknown repo)"


def _format_indexer_search_response_for_slack(*, query: str, payload: dict[str, Any]) -> str:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        return f"No index matches for `{query}`."

    lines: list[str] = [f"Repo index matches for `{query}`:"]
    for idx, item in enumerate(results[:3], start=1):
        if not isinstance(item, dict):
            continue
        repo_payload = item.get("repo")
        repo_payload = repo_payload if isinstance(repo_payload, dict) else {}
        label = _indexer_search_repo_label(repo_payload)
        try:
            score_text = f"{float(item.get('score') or 0.0):.3f}"
        except Exception:
            score_text = "n/a"
        lines.append(f"{idx}. {label} (score {score_text})")

        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        for evidence_item in evidence[:2]:
            if not isinstance(evidence_item, dict):
                continue
            file_path = str(evidence_item.get("file_path") or "").strip() or "(file unknown)"
            start_line = evidence_item.get("start_line")
            end_line = evidence_item.get("end_line")
            location = ""
            if isinstance(start_line, int) and isinstance(end_line, int):
                location = f":{start_line}-{end_line}"
            elif isinstance(start_line, int):
                location = f":{start_line}"
            snippet = _truncate_inline_text(str(evidence_item.get("text") or ""), limit=140)
            if snippet:
                lines.append(f"- `{file_path}{location}` {snippet}")
            else:
                lines.append(f"- `{file_path}{location}`")
    return "\n".join(lines[:15]).strip()


def _intro_message(settings: Any) -> str:
    app_name = (settings.app_display_name or "PRFactory").strip() or "PRFactory"
    slack_install_url = settings.slack_oauth_install_url_resolved()
    github_line = "- connect GitHub when prompted in-thread (or run `/prfactory-github`)"
    indexer_line = (
        f"- search indexed repos: `{INDEXER_SLASH_COMMAND} <query>`"
        if settings.repo_indexer_enabled()
        else ""
    )
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
        f"Hi! I am {app_name}. Use `{PRIMARY_SLASH_COMMAND} <full context request>` in this channel and I will:",
        "- capture your full prompt and ask for a short title first,",
        "- collect details in-thread,",
        "- create a tracked feature request,",
        "- and start a build that opens a PR for review.",
        github_line,
    ]
    if indexer_line:
        lines.append(indexer_line)
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
        "edit_scope": "",
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
    for field in [
        "title",
        "problem",
        "business_justification",
        "acceptance_criteria",
        "implementation_mode",
        "source_repos",
        "edit_scope",
    ]:
        if field in missing:
            ordered_missing.append(field)

    if ordered_missing:
        return ordered_missing
    return list(UPDATE_FALLBACK_FIELDS)


def _peek_next_field(session: IntakeSession) -> str:
    is_model_flow = str(session.answers.get("_flow") or "").strip() == "model"
    for current in list(session.queue):
        if current == "base_branch":
            repo_value = str(session.answers.get("repo") or session.base_spec.get("repo") or "").strip()
            if is_model_flow and repo_value:
                pass  # Model flow: keep base_branch when repo is set
            elif _session_intake_mode(session) != INTAKE_MODE_DEVELOPER or not repo_value:
                continue
        if current == "edit_scope":
            mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
            if mode != "reuse_existing":
                continue
        if current == "source_repos":
            mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
            if mode != "reuse_existing":
                continue
        return current
    return ""


def _next_field(session: IntakeSession) -> str:
    is_model_flow = str(session.answers.get("_flow") or "").strip() == "model"
    while session.queue:
        current = session.queue[0]
        if current == "base_branch":
            repo_value = str(session.answers.get("repo") or session.base_spec.get("repo") or "").strip()
            if is_model_flow and repo_value:
                pass  # Model flow: keep base_branch when repo is set
            elif _session_intake_mode(session) != INTAKE_MODE_DEVELOPER or not repo_value:
                session.queue.pop(0)
                continue
        if current == "edit_scope":
            mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
            if mode != "reuse_existing":
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
    if session.mode != "create":
        return False
    if _session_intake_mode(session) == INTAKE_MODE_DEVELOPER:
        return True
    return _peek_next_field(session) in {"repo", "base_branch"}


def _branch_selection_mutable(session: IntakeSession) -> bool:
    if session.mode != "create":
        return False
    if _session_intake_mode(session) == INTAKE_MODE_DEVELOPER:
        return True
    return _peek_next_field(session) == "base_branch"


def _drop_field_from_queue(session: IntakeSession, field: str) -> None:
    session.queue = [item for item in session.queue if item != field]


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
                    module_logger.error(
                        "slack_repo_prompt_update_failed channel=%s thread=%s ts=%s",
                        session.channel_id,
                        session.thread_ts,
                        existing_ts,
                        exc_info=True,
                    )
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
            text = (
                "Select an existing base branch. PRFactory always creates a new work branch automatically."
            )
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
                    module_logger.error(
                        "slack_branch_prompt_update_failed channel=%s thread=%s ts=%s",
                        session.channel_id,
                        session.thread_ts,
                        existing_ts,
                        exc_info=True,
                    )
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
        branch = _normalize_branch_name(text)
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

        if _is_skip(text) or not text:
            session.answers["source_repos"] = []
            session.asked_fields.add("source_repos")
            return True, "No external reference repos provided; I will use the target repo context only."

        repos = _parse_lines(text)
        if not repos:
            return False, "Reuse mode needs at least one reference repo. Please provide one per line."
        session.answers["source_repos"] = repos
        session.asked_fields.add("source_repos")
        return True, f"Saved {len(repos)} source repo reference(s)."

    if field == "edit_scope":
        mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
        if mode != "reuse_existing":
            return True, "Edit targeting details are not needed for scratch mode."
        if not text or _is_skip(text):
            session.answers["edit_scope"] = "Focus on existing modules and files most directly related to the request."
            session.asked_fields.add("edit_scope")
            return True, "No explicit edit target provided; I will infer likely files from context."
        session.answers["edit_scope"] = text
        session.asked_fields.add("edit_scope")
        return True, "Captured edit targeting details."

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
    seed_prompt = str(spec.get("_seed_prompt") or "").strip()
    for key in [item for item in spec.keys() if str(item).startswith("_")]:
        spec.pop(key, None)

    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    spec["implementation_mode"] = mode
    spec["repo"] = str(spec.get("repo") or "").strip()
    spec["base_branch"] = str(spec.get("base_branch") or "").strip()
    spec["edit_scope"] = str(spec.get("edit_scope") or "").strip()
    spec["source_repos"] = [str(x).strip() for x in (spec.get("source_repos") or []) if str(x).strip()]
    spec["links"] = [str(x).strip() for x in (spec.get("links") or []) if str(x).strip()]
    if not str(spec.get("problem") or "").strip():
        if seed_prompt:
            spec["problem"] = seed_prompt
        else:
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
        problem = str(spec.get("problem") or "").strip()
        problem_excerpt = problem[:160].rstrip() if problem else title
        criteria = [
            f"Implements requested behavior: {problem_excerpt}.",
            "Changes are committed and opened as a PR for review.",
        ]
    spec["acceptance_criteria"] = criteria

    if mode == "reuse_existing" and not str(spec.get("edit_scope") or "").strip():
        spec["edit_scope"] = "Focus on existing modules and files that directly implement this request."

    return spec


def _update_patch_from_session(session: IntakeSession) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field in sorted(session.asked_fields):
        if field in session.answers:
            patch[field] = session.answers[field]
    return patch


def _finalize_session(client: Any, settings: Any, session: IntakeSession) -> None:
    if session.mode == "create":
        _finalize_create_session(client, settings, session)
        return
    _finalize_update_session(client, settings, session)


def _handle_hardcoded_intake_message(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    event: dict[str, Any],
) -> None:
    field = _next_field(session)
    if not field:
        _drop_session(session)
        return

    require_repo = session.mode == "create" and _repo_required_for_slack_intake(settings)
    ok, note = _capture_field_answer(session, field=field, event=event, require_repo=require_repo)
    if not ok:
        client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=note)
        _ask_next_question(client, session)
        return

    if session.queue:
        session.queue.pop(0)
    _store_session(session)

    if note:
        client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=note)

    if _next_field(session):
        _ask_next_question(client, session)
        return

    _finalize_session(client, settings, session)


def _handle_model_intake_action(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    event: dict[str, Any],
    action: IntakeAction,
    tier: str = "mini",
) -> bool:
    action_name = str(getattr(action, "action", "") or "").strip().lower()
    model_name = str(
        getattr(action, "model", "")
        or getattr(action, "model_name", "")
        or getattr(action, "resolved_model", "")
        or ""
    ).strip()
    user_skill = _normalize_user_skill(str(getattr(action, "user_skill", "technical") or "technical"))
    target_field = _normalize_router_field_name(
        str(getattr(action, "field_name", "") or _next_field(session) or "").strip()
    )
    field_value = str(getattr(action, "field_value", "") or "").strip()
    next_question = str(getattr(action, "next_question", "") or "").strip()
    reasoning = str(getattr(action, "reasoning", "") or "").strip()
    suggested_repo = str(
        getattr(action, "suggested_repo", "")
        or (field_value if target_field == "repo" else "")
        or ""
    ).strip()
    suggested_branch = str(
        getattr(action, "suggested_branch", "")
        or (field_value if target_field == "base_branch" else "")
        or ""
    ).strip()

    if action_name == "ask_field":
        if target_field == "github_reauth":
            _post_github_connection_prompt(
                client,
                settings,
                session,
                user_id=str(event.get("user") or session.user_id or "").strip(),
                team_id=str(event.get("team") or session.team_id or "").strip(),
                mode="reauth",
            )
            return True
        if target_field == "github_connect":
            _post_github_connection_prompt(
                client,
                settings,
                session,
                user_id=str(event.get("user") or session.user_id or "").strip(),
                team_id=str(event.get("team") or session.team_id or "").strip(),
                mode="connect",
            )
            return True
        if target_field == "repo":
            # If repo is already collected, skip to next missing field
            if str(session.answers.get("repo") or "").strip():
                _drop_field_from_queue(session, "repo")
                _store_session(session)
                if _next_field(session):
                    _ask_next_question(client, session)
                    return True
                _finalize_session(client, settings, session)
                return True
            _show_repo_dropdown_message(
                client,
                settings,
                session,
                question=next_question or "Which repository should this go in?",
                suggested=suggested_repo or None,
                user_skill=user_skill,
                model_name=model_name,
            )
            return True
        if target_field == "base_branch":
            # If branch is already collected, skip to next missing field
            if str(session.answers.get("base_branch") or "").strip():
                _drop_field_from_queue(session, "base_branch")
                _store_session(session)
                if _next_field(session):
                    _ask_next_question(client, session)
                    return True
                _finalize_session(client, settings, session)
                return True
            _show_branch_dropdown_message(
                client,
                settings,
                session,
                question=next_question or "Which branch should we build from?",
                suggested=suggested_branch or None,
                user_skill=user_skill,
                model_name=model_name,
            )
            return True
        if target_field and field_value:
            require_repo = target_field == "repo" and session.mode == "create" and _repo_required_for_slack_intake(settings)
            ok, note = _capture_field_answer(
                session,
                field=target_field,
                event={"text": field_value, "files": event.get("files") or []},
                require_repo=require_repo,
            )
            if not ok:
                client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=note)
                _ask_next_question(client, session)
                return True
            _drop_field_from_queue(session, target_field)
            _store_session(session)
        if next_question:
            _post_model_next_question(
                client,
                session=session,
                settings=settings,
                question=next_question,
                user_skill=user_skill,
                field_name=target_field,
                tier=tier,
                model_name=model_name,
            )
            return True
        if _next_field(session):
            _ask_next_question(client, session)
            return True
        _finalize_session(client, settings, session)
        return True

    if action_name == "confirm":
        if target_field and field_value:
            require_repo = target_field == "repo" and session.mode == "create" and _repo_required_for_slack_intake(settings)
            ok, note = _capture_field_answer(
                session,
                field=target_field,
                event={"text": field_value, "files": event.get("files") or []},
                require_repo=require_repo,
            )
            if not ok:
                client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=note)
                _ask_next_question(client, session)
                return True
            _drop_field_from_queue(session, target_field)
            _store_session(session)
        _finalize_session(client, settings, session)
        return True

    if action_name == "clarify":
        _post_model_next_question(
            client,
            session=session,
            settings=settings,
            question=next_question or "I need a little more detail before I can continue.",
            user_skill=user_skill,
            field_name=target_field,
            tier=tier,
            model_name=model_name,
        )
        return True

    if action_name == "cancel":
        _drop_session(session)
        _post_thread_message_with_optional_model_context(
            client,
            channel_id=session.channel_id,
            thread_ts=session.thread_ts,
            text=f"Intake cancelled. Use `{PRIMARY_SLASH_COMMAND}` to start again.",
            settings=settings,
            tier=tier,
            model_name=model_name,
        )
        return True

    if action_name == "escalate":
        escalation_text = reasoning or "This request needs a deeper look."
        _post_model_next_question(
            client,
            session=session,
            settings=settings,
            question="This request needs a deeper look.",
            user_skill=user_skill,
            field_name=target_field,
            tier=tier,
            model_name=model_name,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":rotating_light: *This request needs a deeper look.*\n" f"_{escalation_text}_",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Continue with AI analyst"},
                            "action_id": "ff_escalate_frontier",
                            "style": "primary",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Tag a human"},
                            "action_id": "ff_escalate_human",
                        },
                    ],
                },
            ],
        )
        return True

    return False


def _process_session_message(
    client: Any,
    logger: Any,
    settings: Any,
    session: IntakeSession,
    *,
    event: dict[str, Any],
    user_id: str,
    team_id: str,
    channel_id: str,
    thread_ts: str,
    text: str,
    subtype: str | None,
) -> None:
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
    if str(session.answers.get("_intake_paused_reason") or "").strip() == "human":
        logger.info(
            "slack_intake_paused_for_human team=%s channel=%s thread=%s user=%s",
            team_id,
            channel_id,
            thread_ts,
            user_id,
        )
        return
    if _waiting_for_github(session):
        if "/" in text:
            ok, note = _capture_field_answer(
                session,
                field="repo",
                event={"text": text, "files": event.get("files") or []},
                require_repo=session.mode == "create" and _repo_required_for_slack_intake(settings),
            )
            if ok:
                _set_waiting_for_github(session, enabled=False)
                _drop_field_from_queue(session, "repo")
                _store_session(session)
                if note:
                    client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=note)
                if _next_field(session):
                    _ask_next_question(client, session)
                else:
                    _finalize_session(client, settings, session)
                return
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=note)
            return
        if not HAS_GITHUB_CONNECTION_CHECKER or check_github_connection is None:
            _set_waiting_for_github(session, enabled=False)
            _store_session(session)
        else:
            snapshot = _github_connection_snapshot_sync(user_id=user_id, team_id=team_id)
            status = str(snapshot.get("status") or "").strip().lower()
            if status == "connected":
                _set_waiting_for_github(session, enabled=False)
                _store_session(session)
                _show_repo_dropdown_message(
                    client,
                    settings,
                    session,
                    question="GitHub reconnected! Here are your repos:",
                    suggested=None,
                    user_skill=_stored_model_user_skill(session),
                    model_name=_stored_model_name(session),
                )
                return
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="Still waiting for GitHub connection. Click the button above, or type a repo name like `org/repo`.",
            )
            return

    # ---- Determine flow: model vs hardcoded ----
    print(f"[PRFACTORY DIAG] _process_session_message: flow={session.answers.get('_flow')}, field={_next_field(session)}, HAS_INTAKE_ROUTER={HAS_INTAKE_ROUTER}, openrouter={_openrouter_enabled(settings)}", flush=True)
    session_flow = str(session.answers.get("_flow") or "").strip()
    _try_model = False
    if session_flow == "model":
        _try_model = True
    elif session_flow == "hardcoded":
        _try_model = False
    else:
        # Legacy session (no _flow marker) — try model if available
        _try_model = HAS_INTAKE_ROUTER and _openrouter_enabled(settings)

    if _try_model:
        # ---- Affirmation shortcut: accept a previously suggested repo ----
        normalized_text = text.strip().lower().rstrip(".!,")
        if normalized_text in AFFIRMATION_PHRASES:
            suggested_repo = session.answers.get("_suggested_repo")
            if suggested_repo and not session.answers.get("repo"):
                session.answers["repo"] = suggested_repo
                session.asked_fields.add("repo")
                _drop_field_from_queue(session, "repo")
                session.answers.pop("_suggested_repo", None)
                _store_session(session)
                _advance_to_next_field(client, settings, session)
                return

        logger.info(
            "slack_intake_path=model_assisted team=%s channel=%s thread=%s user=%s",
            team_id,
            channel_id,
            thread_ts,
            user_id,
        )
        thread_messages = _fetch_thread_messages(client, channel_id=channel_id, thread_ts=thread_ts, logger=logger)
        conversation_history = _build_thread_history(thread_messages)
        if not conversation_history:
            conversation_history = _build_thread_history([event])

        # Build current fields — include seed prompt as context for the model
        current_fields = {
            k: v for k, v in dict(session.answers or {}).items()
            if not str(k).startswith("_") and v is not None
        }
        seed = str(session.answers.get("_seed_prompt") or "").strip()
        if seed:
            current_fields["original_request"] = seed

        try:
            action = _classify_intake_message_sync(
                message=text,
                conversation_history=conversation_history,
                current_fields=current_fields,
                slack_user_id=user_id,
            )
            if _handle_model_intake_action(client, settings, session, event=event, action=action):
                return
            logger.warning(
                "slack_intake_unknown_action team=%s channel=%s thread=%s user=%s action=%s",
                team_id,
                channel_id,
                thread_ts,
                user_id,
                getattr(action, "action", ""),
            )
        except Exception:
            import traceback as _tb
            print(f"[PRFACTORY DIAG] Model reply FAILED:\n{_tb.format_exc()}", flush=True)
            logger.error(
                "slack_model_intake_failed team=%s channel=%s thread=%s user=%s",
                team_id,
                channel_id,
                thread_ts,
                user_id,
                exc_info=True,
            )
            # Post a visible transition so the UX switch isn't jarring
            try:
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="I had trouble processing that. Let me ask you directly instead.",
                )
            except Exception:  # noqa: BLE001
                pass

    logger.info(
        "slack_intake_path=hardcoded team=%s channel=%s thread=%s user=%s",
        team_id,
        channel_id,
        thread_ts,
        user_id,
    )
    _handle_hardcoded_intake_message(client, settings, session, event=event)


def _start_create_intake(
    client: Any,
    settings: Any,
    *,
    team_id: str,
    channel_id: str,
    user_id: str,
    seed_prompt: str,
) -> None:
    print(f"[PRFACTORY DIAG] _start_create_intake called. HAS_INTAKE_ROUTER={HAS_INTAKE_ROUTER}, openrouter_enabled={_openrouter_enabled(settings)}, seed_prompt={repr((seed_prompt or '')[:50])}", flush=True)
    msg = client.chat_postMessage(
        channel=channel_id,
        text=f"Got it - feature request intake started by <@{user_id}>. Reply in this thread.",
    )
    thread_ts = msg["ts"]

    answers: dict[str, Any] = {}
    answers["_intake_mode"] = INTAKE_MODE_NORMAL
    normalized_seed_prompt = (seed_prompt or "").strip()
    if normalized_seed_prompt:
        answers["_seed_prompt"] = normalized_seed_prompt[:2000]

    require_repo = _repo_required_for_slack_intake(settings)
    use_model = HAS_INTAKE_ROUTER and _openrouter_enabled(settings)

    session = IntakeSession(
        mode="create",
        feature_id="",
        user_id=user_id,
        team_id=team_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_ts=thread_ts,
        queue=_build_create_queue(
            has_title=False,
            require_repo=require_repo,
            minimal=bool(settings.slack_intake_minimal),
        ),
        answers=answers,
    )

    # ---- Model-assisted startup ----
    if use_model:
        session.answers["_flow"] = "model"
        _store_session(session)

        if normalized_seed_prompt:
            # User typed "/prfactory I want to add dark mode..." — run the
            # model immediately with the seed prompt as the first message.
            thinking_msg = client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=":hourglass_flowing_sand: Analyzing your request...",
            )
            thinking_ts = str(thinking_msg.get("ts") or "").strip()
            try:
                print(f"[PRFACTORY DIAG] About to call _classify_intake_message_sync with seed prompt", flush=True)
                action = _classify_intake_message_sync(
                    message=normalized_seed_prompt,
                    conversation_history=[],
                    current_fields={},
                    slack_user_id=user_id,
                )
                # Remove thinking indicator
                if thinking_ts:
                    try:
                        client.chat_delete(channel=channel_id, ts=thinking_ts)
                    except Exception:
                        try:
                            client.chat_update(channel=channel_id, ts=thinking_ts, text=" ")
                        except Exception:
                            pass
                if _handle_model_intake_action(
                    client,
                    settings,
                    session,
                    event={"text": normalized_seed_prompt, "user": user_id, "team": team_id},
                    action=action,
                ):
                    return
                # Model returned an unrecognized action — fall through to
                # open-ended greeting below.
            except Exception:
                import traceback as _tb
                print(f"[PRFACTORY DIAG] Model startup FAILED:\n{_tb.format_exc()}", flush=True)
                # Remove thinking indicator on failure too
                if thinking_ts:
                    try:
                        client.chat_delete(channel=channel_id, ts=thinking_ts)
                    except Exception:
                        try:
                            client.chat_update(channel=channel_id, ts=thinking_ts, text=" ")
                        except Exception:
                            pass
                module_logger.error(
                    "slack_model_intake_startup_failed team=%s channel=%s thread=%s user=%s",
                    team_id, channel_id, thread_ts, user_id,
                    exc_info=True,
                )
                module_logger.info("slack_model_intake_falling_back_to_hardcoded team=%s channel=%s", team_id, channel_id)
                session.answers["_flow"] = "hardcoded"
                session.answers.pop("_seed_prompt", None)
                _store_session(session)
                # Fall through to the hardcoded path below.
                return _start_create_intake_hardcoded(
                    client, settings, session,
                    normalized_seed_prompt=normalized_seed_prompt,
                    user_id=user_id, team_id=team_id,
                )

        # No seed prompt — post an open-ended model greeting.
        controls_message = client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="What would you like to build? Describe the feature and I'll collect the details.",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "plain_text",
                        "text": "What would you like to build? Describe the feature and I'll collect the details.",
                    },
                },
                *_intake_controls_blocks(mode=_session_intake_mode(session)),
            ],
        )
        session.answers["_controls_message_ts"] = str(controls_message.get("ts") or "").strip()
        _store_session(session)
        return

    # ---- Hardcoded startup (no model available) ----
    session.answers["_flow"] = "hardcoded"
    _store_session(session)
    _start_create_intake_hardcoded(
        client, settings, session,
        normalized_seed_prompt=normalized_seed_prompt,
        user_id=user_id, team_id=team_id,
    )


def _start_create_intake_hardcoded(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    normalized_seed_prompt: str,
    user_id: str,
    team_id: str,
) -> None:
    """Post the original hardcoded title question and control blocks."""
    controls_message = client.chat_postMessage(
        channel=session.channel_id,
        thread_ts=session.thread_ts,
        text=QUESTION_BY_FIELD["title"],
        blocks=_title_prompt_blocks(
            mode=_session_intake_mode(session),
            seed_prompt=normalized_seed_prompt,
            github_status_block=_github_connection_context_block(user_id=user_id, team_id=team_id),
        ),
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
    answers: dict[str, Any] = {"_intake_mode": INTAKE_MODE_DEVELOPER}
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


def _action_root_message_ts(body: dict[str, Any]) -> str:
    message = body.get("message") or {}
    return str(message.get("thread_ts") or message.get("ts") or "").strip()


def _human_escalation_target(settings: Any) -> str:
    reviewers = sorted(settings.reviewer_allowed_user_set())
    if reviewers:
        return " ".join(f"<@{reviewer}>" for reviewer in reviewers)
    reviewer_channel = str(settings.reviewer_channel_id or "").strip()
    if reviewer_channel:
        return f"<#{reviewer_channel}>"
    return "<!channel>"


def _apply_repo_selection(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    team_id: str,
    channel_id: str,
    user_id: str,
    selected: str,
) -> None:
    thread_ts = session.thread_ts

    if selected == REPO_OPTION_CONNECT:
        install_url = _github_connect_url_for_user(settings, user_id=user_id, team_id=team_id)
        indexer_url = _indexer_connect_url_for_user(settings, user_id=user_id, team_id=team_id)
        if install_url and indexer_url and install_url != indexer_url:
            text = (
                "Connect GitHub first:\n"
                f"- PRFactory OAuth: {install_url}\n"
                f"- Repo_Indexer OAuth: {indexer_url}"
            )
        elif install_url:
            text = f"Connect GitHub first: {install_url}"
        elif indexer_url:
            text = f"Connect GitHub in Repo_Indexer: {indexer_url}"
        else:
            text = "GitHub connect link is unavailable."
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
        _drop_field_from_queue(session, "repo")
        session.answers.pop("base_branch", None)
        session.asked_fields.discard("base_branch")
        _drop_field_from_queue(session, "base_branch")
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
    repo_changed = repo_slug != previous_repo
    session.answers["repo"] = repo_slug
    session.asked_fields.add("repo")
    _drop_field_from_queue(session, "repo")
    if repo_changed:
        session.answers.pop("base_branch", None)
        session.asked_fields.discard("base_branch")
        if _session_intake_mode(session) == INTAKE_MODE_DEVELOPER:
            _drop_field_from_queue(session, "base_branch")
            session.queue.insert(0, "base_branch")
        session.answers.pop("_branch_prompt_ts", None)
    _store_session(session)
    if repo_changed and previous_repo:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"Updated repo: `{repo_slug}`. Branch options refreshed.",
        )
    else:
        client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Captured repo: `{repo_slug}`")
    if _next_field(session) == "base_branch":
        try:
            default_branch = _fetch_default_branch_for_repo(
                settings,
                user_id=user_id,
                team_id=team_id,
                repo_slug=repo_slug,
            )
            branches = _fetch_branches_for_repo(
                settings,
                user_id=user_id,
                team_id=team_id,
                repo_slug=repo_slug,
            )
        except GitHubAuthError:
            _post_github_connection_prompt(
                client,
                settings,
                session,
                user_id=user_id,
                team_id=team_id,
                mode="reauth",
            )
            return
        if default_branch and _is_autogenerated_branch(settings, default_branch):
            fallback = _stable_branch_fallback(settings, branches=branches, default_branch=default_branch)
            if fallback and fallback.lower() != default_branch.lower():
                detail = f"I will auto-fallback to `{fallback}` if you choose *None (use default branch)*."
            else:
                detail = "Choose a stable base branch explicitly (for example `main` or `develop`) when possible."
            client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=(
                    f"Repository default branch is `{default_branch}`, which looks auto-generated.\n"
                    f"{detail}"
                ),
            )
    if _next_field(session):
        _ask_next_question(client, session)
    elif session.mode == "create":
        _finalize_create_session(client, settings, session)
    else:
        _finalize_update_session(client, settings, session)


def _apply_branch_selection(
    client: Any,
    settings: Any,
    session: IntakeSession,
    *,
    team_id: str,
    channel_id: str,
    user_id: str,
    selected: str,
) -> None:
    thread_ts = session.thread_ts
    repo_slug = str(session.answers.get("repo") or "").strip()
    if not repo_slug:
        client.chat_postEphemeral(channel=channel_id, user=user_id, text="Select a repo first, then choose a base branch.")
        return

    if selected == BRANCH_OPTION_NEW:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Reply in thread with an existing base branch name (example: `main` or `develop`).",
        )
        return

    if selected in {BRANCH_OPTION_NONE, BRANCH_OPTION_AUTOGEN}:
        default_branch = _fetch_default_branch_for_repo(
            settings,
            user_id=user_id,
            team_id=team_id,
            repo_slug=repo_slug,
        )
        branches = _fetch_branches_for_repo(
            settings,
            user_id=user_id,
            team_id=team_id,
            repo_slug=repo_slug,
        )
        fallback = _stable_branch_fallback(settings, branches=branches, default_branch=default_branch)
        if default_branch and _is_autogenerated_branch(settings, default_branch):
            if fallback and fallback.lower() != default_branch.lower():
                session.answers["base_branch"] = fallback
                session.asked_fields.add("base_branch")
                branch_note = (
                    f"Repository default `{default_branch}` looks auto-generated.\n"
                    f"Using stable fallback base branch `{fallback}`.\n"
                    "PRFactory will still create a new `prfactory/...` work branch for this request."
                )
            else:
                session.answers["base_branch"] = ""
                branch_note = (
                    f"Using repository default base branch `{default_branch}`.\n"
                    "Warning: this looks like an auto-generated PRFactory branch. "
                    "Set a stable default branch in GitHub (for example `main` or `develop`) "
                    "or choose a branch explicitly.\n"
                    "PRFactory will still create a new `prfactory/...` work branch for this request."
                )
        elif default_branch:
            session.answers["base_branch"] = ""
            branch_note = (
                f"Using repository default base branch `{default_branch}`.\n"
                "PRFactory will create a new `prfactory/...` work branch for this request."
            )
        else:
            session.answers["base_branch"] = ""
            branch_note = (
                "Using repository default base branch.\n"
                "PRFactory will create a new `prfactory/...` work branch for this request."
            )
        _drop_field_from_queue(session, "base_branch")
        _store_session(session)
        client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=branch_note)
        if _next_field(session):
            _ask_next_question(client, session)
        elif session.mode == "create":
            _finalize_create_session(client, settings, session)
        else:
            _finalize_update_session(client, settings, session)
        return

    selected = _normalize_branch_name(selected)
    if not re.match(r"^[A-Za-z0-9._/-]+$", selected):
        client.chat_postEphemeral(channel=channel_id, user=user_id, text="Invalid branch name.")
        return
    session.answers["base_branch"] = selected
    session.asked_fields.add("base_branch")
    _drop_field_from_queue(session, "base_branch")
    _store_session(session)
    note = f"Base branch set to `{selected}`."
    if _is_autogenerated_branch(settings, selected):
        note = (
            f"{note}\nWarning: this looks like an auto-generated PRFactory branch. "
            "Prefer a stable branch such as `main`/`develop` when available."
        )
    client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=note)
    if _next_field(session):
        _ask_next_question(client, session)
    elif session.mode == "create":
        _finalize_create_session(client, settings, session)
    else:
        _finalize_update_session(client, settings, session)


def _latest_user_message_from_history(
    conversation_history: list[dict[str, str]],
    *,
    fallback: str = "",
) -> str:
    for item in reversed(conversation_history):
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            return content
    return str(fallback or "").strip()


def _handle_frontier_escalation(
    client: Any,
    logger: Any,
    settings: Any,
    session: IntakeSession,
    *,
    user_id: str,
) -> None:
    if not HAS_ESCALATE:
        client.chat_postMessage(
            channel=session.channel_id,
            thread_ts=session.thread_ts,
            text="The AI analyst isn't available right now. I can keep going here, or you can tag a human reviewer.",
        )
        return

    thread_messages = _fetch_thread_messages(
        client,
        channel_id=session.channel_id,
        thread_ts=session.thread_ts,
        logger=logger,
    )
    conversation_history = _build_thread_history(thread_messages)
    latest_message = _latest_user_message_from_history(
        conversation_history,
        fallback=str(session.answers.get("_seed_prompt") or ""),
    )

    try:
        frontier_action = _escalate_to_frontier_sync(
            message=latest_message,
            conversation_history=conversation_history,
            current_fields=dict(session.answers or {}),
            slack_user_id=user_id,
        )
    except Exception:
        logger.error(
            "slack_frontier_escalation_failed channel=%s thread=%s user=%s",
            session.channel_id,
            session.thread_ts,
            user_id,
            exc_info=True,
        )
        client.chat_postMessage(
            channel=session.channel_id,
            thread_ts=session.thread_ts,
            text="I couldn't reach the AI analyst right now. Please try again, or tag a human reviewer.",
        )
        if _next_field(session):
            _ask_next_question(client, session)
        return

    if _handle_model_intake_action(
        client,
        settings,
        session,
        event={"text": latest_message, "files": []},
        action=frontier_action,
        tier="frontier",
    ):
        return

    client.chat_postMessage(
        channel=session.channel_id,
        thread_ts=session.thread_ts,
        text="The AI analyst reviewed it, but I still need a little more detail to continue.",
    )


def _handle_human_escalation(client: Any, settings: Any, session: IntakeSession) -> None:
    session.answers["_intake_paused_reason"] = "human"
    _store_session(session)
    client.chat_postMessage(
        channel=session.channel_id,
        thread_ts=session.thread_ts,
        text=(
            f"{_human_escalation_target(settings)} Human review requested for this intake. "
            "The AI intake is paused for now."
        ),
    )


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


def _post_prompt_confirmation_message(
    client: Any,
    *,
    channel_id: str,
    thread_ts: str,
    feature_id: str,
    original_request: str,
    optimized_prompt: str,
    settings: Any | None = None,
) -> None:
    def _preview_block(text: str, *, max_chars: int = 1000, empty_fallback: str) -> str:
        value = str(text or "").strip()
        if len(value) > max_chars:
            value = value[: max_chars - 3].rstrip() + "..."
        return value or empty_fallback

    original_preview = _preview_block(
        original_request,
        max_chars=1000,
        empty_fallback="(original request not provided)",
    )
    optimized_preview = _preview_block(
        optimized_prompt,
        max_chars=1200,
        empty_fallback="(optimized prompt is empty)",
    )
    text = (
        "Request is ready for build.\n"
        "For transparency, review both the original request and the optimized prompt before running."
    )
    resolved_settings = settings or get_settings()
    _post_thread_message_with_optional_model_context(
        client,
        channel_id=channel_id,
        thread_ts=thread_ts,
        text=text,
        settings=resolved_settings,
        tier="frontier",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Ready for build*"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Original request*\n```{original_preview}```",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Optimized build prompt*\n```{optimized_preview}```",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "ff_run_build",
                        "text": {"type": "plain_text", "text": "Run build"},
                        "style": "primary",
                        "value": feature_id,
                    },
                    {
                        "type": "button",
                        "action_id": "ff_add_details",
                        "text": {"type": "plain_text", "text": "Add more context"},
                        "value": feature_id,
                    },
                ],
            },
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
    try:
        client.chat_update(
            channel=session.channel_id,
            ts=session.message_ts,
            text=f"Feature request: *{title}*",
            blocks=blocks,
        )
    except Exception as e:  # noqa: BLE001
        console.print(
            f"[yellow]slack_feature_message_update_failed feature={feature.get('id')} "
            f"channel={session.channel_id} ts={session.message_ts} error={e}[/yellow]"
        )

    _post_thread_message_with_optional_model_context(
        client,
        channel_id=session.channel_id,
        thread_ts=session.thread_ts,
        settings=settings,
        tier="frontier",
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
        feature_id = str(feature.get("id") or "")
        if bool(settings.slack_require_prompt_confirmation):
            original_request = str((feature.get("spec") or {}).get("problem") or "").strip()
            optimized_prompt = str((feature.get("spec") or {}).get("optimized_prompt") or "").strip()
            _post_prompt_confirmation_message(
                client,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
                feature_id=feature_id,
                original_request=original_request,
                optimized_prompt=optimized_prompt,
                settings=settings,
            )
            client.chat_postMessage(
                channel=session.channel_id,
                thread_ts=session.thread_ts,
                text="Build is paused for confirmation. Click *Run build* when ready.",
            )
            return

        ok, note, payload = _enqueue_build_for_feature(
            settings,
            feature_id=feature_id,
            actor_id=session.user_id,
            actor_type="slack",
            message="Build auto-started from Slack intake",
        )
        client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=note)
        try:
            refreshed = _fetch_feature(settings, feature_id)
            _update_feature_message(
                client,
                refreshed,
                channel_id=session.channel_id,
                message_ts=session.message_ts,
            )
        except Exception:
            module_logger.error(
                "slack_feature_refresh_after_create_failed feature=%s channel=%s",
                feature_id,
                session.channel_id,
                exc_info=True,
            )
        if not ok:
            install_url = str((payload or {}).get("install_url") or "").strip()
            _post_build_retry_message(
                client,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
                feature_id=feature_id,
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

    _post_thread_message_with_optional_model_context(
        client,
        channel_id=session.channel_id,
        thread_ts=session.thread_ts,
        settings=settings,
        tier="frontier",
        text=f"Updated request `{feature['id']}`. Status is now `{feature['status']}`.",
    )
    if feature.get("status") == "NEEDS_INFO":
        _post_clarification_prompt(client, session.channel_id, session.thread_ts, feature)
    elif feature.get("status") == "READY_FOR_BUILD":
        _post_thread_message_with_optional_model_context(
            client,
            channel_id=session.channel_id,
            thread_ts=session.thread_ts,
            settings=settings,
            tier="frontier",
            text="Spec looks complete. Click *Run build* when ready.",
        )


def create_slack_bolt_app(settings: Any):
    from slack_bolt import App

    oauth_runtime = get_slack_oauth_runtime()
    app_kwargs: dict[str, Any] = {
        "signing_secret": settings.slack_signing_secret or "",
        "process_before_response": True,
    }
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
                f"To start in a channel: invite me, then run `{PRIMARY_SLASH_COMMAND} <full context request>`.",
                "I will capture that prompt and ask for a short title first.",
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
            seed_prompt=text,
        )

    def _handle_indexer_command(ack, body, client, logger) -> None:
        ack()
        team_id = str(body.get("team_id") or "").strip()
        channel_id = body.get("channel_id")
        user_id = body.get("user_id")
        query = str(body.get("text") or "").strip()

        allowed_channels = settings.slack_allowed_channel_set()
        allowed_users = settings.slack_allowed_user_set()

        if allowed_channels and channel_id not in allowed_channels:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Not allowed in this channel.")
            return
        if allowed_users and user_id not in allowed_users:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="You are not allowlisted.")
            return

        if not query:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Usage: `{INDEXER_SLASH_COMMAND} <query>`",
            )
            return

        indexer = get_repo_indexer_client(settings=settings)
        if indexer is None:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Repo indexer is not configured. Set `INDEXER_BASE_URL` first.",
            )
            return

        try:
            payload = indexer.search(
                query=query,
                top_k_repos=max(int(getattr(settings, "indexer_top_k_repos", 5) or 5), 1),
                top_k_chunks=max(int(getattr(settings, "indexer_top_k_chunks", 3) or 3), 1),
            )
        except RepoIndexerError as e:
            logger.warning("slack_indexer_search_failed user=%s team=%s error=%s", user_id, team_id, e)
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Repo indexer request failed: {e}",
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("slack_indexer_search_failed user=%s team=%s error=%s", user_id, team_id, e)
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f"Repo indexer request failed: {e}",
            )
            return

        text = _format_indexer_search_response_for_slack(query=query, payload=payload)
        client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)

    @app.command(PRIMARY_SLASH_COMMAND)
    def handle_prfactory(ack, body, client, logger):
        _handle_create_command(ack, body, client, logger)

    @app.command(LEGACY_SLASH_COMMAND)
    def handle_feature_alias(ack, body, client, logger):
        _handle_create_command(ack, body, client, logger)

    @app.command(INDEXER_SLASH_COMMAND)
    def handle_repo_indexer(ack, body, client, logger):
        _handle_indexer_command(ack, body, client, logger)

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
                    f"Run `{PRIMARY_SLASH_COMMAND} <full context request>` to start a build."
                )
            elif install_url:
                text = (
                    f"GitHub connect for {app_name}:\n"
                    f"1. Open {install_url}\n"
                    "2. Authorize access with your own GitHub account\n"
                    f"3. Run `{PRIMARY_SLASH_COMMAND} <full context request>` again"
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
        except GitHubAuthError:
            options = [_slack_option(text="Reconnect GitHub", value=REPO_OPTION_CONNECT)]
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
        except GitHubAuthError:
            options = _fallback_branch_options()
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
                seed_prompt = str(session.answers.get("_seed_prompt") or "").strip()
                client.chat_update(
                    channel=channel_id,
                    ts=controls_ts,
                    text=QUESTION_BY_FIELD["title"],
                    blocks=_title_prompt_blocks(mode=next_mode, seed_prompt=seed_prompt),
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

    @app.action("ff_accept_repo_suggestion")
    def handle_accept_repo_suggestion(ack, body, client):
        ack()
        action = (body.get("actions") or [{}])[0]
        selected = str(action.get("value") or "").strip()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts and selected):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        if not _repo_selection_mutable(session):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Repo selection is locked after build starts.")
            return
        session.answers.pop("_suggested_repo", None)
        _store_session(session)
        _apply_repo_selection(
            client,
            settings,
            session,
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            selected=selected,
        )

    @app.action("ff_show_repo_dropdown")
    def handle_show_repo_dropdown(ack, body, client):
        ack()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        if not _repo_selection_mutable(session):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Repo selection is locked after build starts.")
            return
        _show_repo_dropdown_message(
            client,
            settings,
            session,
            question=str(session.answers.get("_repo_selection_question") or "Which repository should this go in?"),
            suggested=None,
            user_skill=_stored_model_user_skill(session),
            model_name=_stored_model_name(session),
        )

    @app.action("ff_github_reauth")
    def handle_github_reauth(ack, body, client):
        ack()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        _set_waiting_for_github(session, enabled=True, mode="reauth")
        _store_session(session)
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="Browser opened for GitHub reconnect. When you're back, reply in the thread and I'll resume with your repos.",
        )

    @app.action("ff_github_connect")
    def handle_github_connect(ack, body, client):
        ack()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        _set_waiting_for_github(session, enabled=True, mode="connect")
        _store_session(session)
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text="Browser opened for GitHub connect. When you're back, reply in the thread and I'll resume with your repos.",
        )

    @app.action("ff_accept_branch_suggestion")
    def handle_accept_branch_suggestion(ack, body, client):
        ack()
        action = (body.get("actions") or [{}])[0]
        selected = str(action.get("value") or "").strip()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts and selected):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        if not _branch_selection_mutable(session):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Base branch selection is locked after build starts.")
            return
        _apply_branch_selection(
            client,
            settings,
            session,
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            selected=selected,
        )

    @app.action("ff_show_branch_dropdown")
    def handle_show_branch_dropdown(ack, body, client):
        ack()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        if not _branch_selection_mutable(session):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Base branch selection is locked after build starts.")
            return
        _show_branch_dropdown_message(
            client,
            settings,
            session,
            question=str(session.answers.get("_branch_selection_question") or "Which branch should we build from?"),
            suggested=None,
            user_skill=_stored_model_user_skill(session),
            model_name=_stored_model_name(session),
        )

    @app.action("ff_escalate_frontier")
    def handle_escalate_frontier(ack, body, client, logger):
        ack()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        _handle_frontier_escalation(client, logger, settings, session, user_id=user_id)

    @app.action("ff_escalate_human")
    def handle_escalate_human(ack, body, client):
        ack()
        team_id, channel_id, user_id, thread_ts, _message_ts = _action_context(body)
        if not (team_id and channel_id and user_id and thread_ts):
            return
        session = _get_session(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Intake session expired. Run /prfactory again.")
            return
        _handle_human_escalation(client, settings, session)

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
        if not _repo_selection_mutable(session):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Repo selection is locked after build starts.")
            return
        _apply_repo_selection(
            client,
            settings,
            session,
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            selected=selected,
        )

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
        if not _branch_selection_mutable(session):
            client.chat_postEphemeral(channel=channel_id, user=user_id, text="Base branch selection is locked after build starts.")
            return
        _apply_branch_selection(
            client,
            settings,
            session,
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            selected=selected,
        )

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
            if _is_stop_command(text):
                try:
                    feature_id = _disable_stale_alerts_for_thread(
                        team_id=team_id,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                        user_id=user_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "slack_stop_disable_alerts_failed team=%s channel=%s thread=%s user=%s error=%s",
                        team_id,
                        channel_id,
                        thread_ts,
                        user_id,
                        e,
                    )
                    feature_id = ""
                if feature_id:
                    client.chat_postMessage(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text=f"Understood. Stale callback reminders are now muted for request `{feature_id}`.",
                    )
                    return
            logger.debug(
                "slack_message_event_no_session team=%s channel=%s thread=%s user=%s subtype=%s",
                team_id,
                channel_id,
                thread_ts,
                user_id,
                subtype,
            )
            return
        _process_session_message(
            client,
            logger,
            settings,
            session,
            event=event,
            user_id=user_id,
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=text,
            subtype=subtype,
        )

    @app.action("ff_add_details")
    def handle_add_details(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        team_id = str(body.get("team", {}).get("id") or body.get("team_id") or "").strip()
        channel_id = body.get("channel", {}).get("id")
        user_id = body["user"]["id"]
        message_ts = _action_root_message_ts(body)
        thread_ts = message_ts

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
        root_message_ts = _action_root_message_ts(body)

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
                optimistic = dict(current) if isinstance(current, dict) else {}
                if optimistic and root_message_ts:
                    optimistic["status"] = str((payload or {}).get("status") or "BUILDING").strip() or "BUILDING"
                    _update_feature_message(client, optimistic, channel_id=channel_id, message_ts=root_message_ts)
                refreshed = _fetch_feature(settings, feature_id)
                if root_message_ts:
                    _update_feature_message(client, refreshed, channel_id=channel_id, message_ts=root_message_ts)
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

    @app.action("ff_refresh_status")
    def handle_refresh_status(ack, body, client):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        channel_id = body.get("channel", {}).get("id")
        user_id = body.get("user", {}).get("id")
        root_message_ts = _action_root_message_ts(body)
        if not (channel_id and root_message_ts):
            return
        try:
            refreshed = _fetch_feature(settings, feature_id)
            _update_feature_message(client, refreshed, channel_id=channel_id, message_ts=root_message_ts)
        except Exception as e:  # noqa: BLE001
            if user_id:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f"Could not refresh status: `{e}`",
                )

    @app.action("ff_approve")
    def handle_approve(ack, body, client, logger):
        ack()
        action = body["actions"][0]
        feature_id = action["value"]
        user_id = body["user"]["id"]
        channel_id = body.get("channel", {}).get("id")
        thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts")
        message_ts = _action_root_message_ts(body)

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


def _socket_mode_handler_cls():
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    return SocketModeHandler


def _start_socket_mode_handler(settings: Any) -> None:
    handler_cls = _socket_mode_handler_cls()
    app = create_slack_bolt_app(settings)
    console.print("[green]Starting Slack Socket Mode handler...[/green]")
    handler_cls(app, settings.slack_app_token).start()


def main() -> None:
    settings = get_settings()

    if not settings.enable_slack_bot:
        console.print("[yellow]Slack bot is disabled (ENABLE_SLACK_BOT=false). Sleeping...[/yellow]")
        while True:
            time.sleep(3600)

    if settings.slack_mode_normalized() != "socket":
        module_logger.warning(
            "slackbot container started but SLACK_MODE=%s (not 'socket'). "
            "Set SLACK_MODE=socket in .env or use the API's HTTP Slack handler. "
            "Exiting instead of sleeping forever.",
            settings.slack_mode,
        )
        console.print(
            "[yellow]Slack bot process only runs in socket mode; set SLACK_MODE=socket or run HTTP mode in FastAPI.[/yellow]"
        )
        sys.exit(0)

    if not settings.slack_bot_token or not settings.slack_app_token:
        module_logger.error(
            "slackbot socket mode cannot start because SLACK_BOT_TOKEN or SLACK_APP_TOKEN is missing"
        )
        console.print("[red]Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN. Exiting.[/red]")
        sys.exit(1)

    _start_socket_mode_handler(settings)


if __name__ == "__main__":
    main()
