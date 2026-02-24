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
from app.services.reviewer_service import is_approver_allowed


console = Console()

QUESTION_BY_FIELD: dict[str, str] = {
    "title": "What do you want to build?",
    "problem": "Describe what you want in one short paragraph (what to build + why).",
    "business_justification": "Why is this needed now?",
    "links": "Optional: share links/files in this thread, or reply `skip`.",
    "repo": "Do you know what project/repo this belongs to? Reply with `org/repo`, repo URL, or `unsure`.",
    "implementation_mode": "Should implementation start from scratch or reuse existing project patterns? Reply `scratch` or `reuse`.",
    "source_repos": "If reusing existing patterns, which repos should be references? One per line.",
    "proposed_solution": "Any preferred implementation approach or constraints? Reply `skip` if none.",
    "acceptance_criteria": "Optional: acceptance criteria, one per line. Reply `skip` to use defaults.",
}

CREATE_FLOW_FIELDS = [
    "title",
    "problem",
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
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
SKIP_TOKENS = {"skip", "n/a", "na", "none", "no", "not sure", "unsure", "unknown", "idk"}


@dataclass
class IntakeSession:
    mode: str  # create | update
    feature_id: str
    user_id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    queue: list[str] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)
    asked_fields: set[str] = field(default_factory=set)
    base_spec: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


ACTIVE_INTAKES: dict[str, IntakeSession] = {}


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
    return "Build from scratch"


def _session_key(*, channel_id: str, thread_ts: str, user_id: str) -> str:
    return f"{channel_id}:{thread_ts}:{user_id}"


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
    key = _session_key(channel_id=session.channel_id, thread_ts=session.thread_ts, user_id=session.user_id)
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


def _get_session(*, channel_id: str, thread_ts: str, user_id: str) -> IntakeSession | None:
    _cleanup_expired_sessions()
    key = _session_key(channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
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
    key = _session_key(channel_id=session.channel_id, thread_ts=session.thread_ts, user_id=session.user_id)
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
    spec = feature.get("spec") or {}
    mode = spec.get("implementation_mode", "new_feature")
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
                    f"*{title}*\nStatus: `{status}`\nMode: `{mode}`\nID: `{fid}`\n"
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


def _intro_message(settings: Any) -> str:
    return (
        "Hi! I am Feature Factory. Use `/feature <what you want built>` in this channel and I will:\n"
        "- collect details in-thread,\n"
        "- create a tracked feature request,\n"
        "- and start a build that opens a PR for review.\n"
        f"Dashboard: {settings.base_url}"
    )


def _post_intro_messages(
    client: Any,
    settings: Any,
    *,
    channel_id: str,
    inviter_id: str,
    logger: Any,
) -> None:
    text = _intro_message(settings)
    try:
        client.chat_postMessage(channel=channel_id, text=text)
    except Exception as e:  # noqa: BLE001
        logger.warning("slack_intro_channel_post_failed channel=%s error=%s", channel_id, e)

    if not inviter_id:
        return

    dm_text = (
        "Thanks for adding Feature Factory.\n"
        "Anyone in that channel can now use `/feature` to request work and track build/PR progress."
    )
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
        "implementation_mode": "new_feature",
        "source_repos": [],
        "risk_flags": [],
        "links": [],
        "debug_build": False,
    }


def _build_create_queue(*, has_title: bool) -> list[str]:
    queue = list(CREATE_FLOW_FIELDS)
    if has_title:
        queue.remove("title")
    return queue


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
        if current == "source_repos":
            mode = str(session.answers.get("implementation_mode") or session.base_spec.get("implementation_mode") or "new_feature")
            if mode != "reuse_existing":
                session.queue.pop(0)
                continue
        return current
    return ""


def _ask_next_question(client: Any, session: IntakeSession) -> None:
    field = _next_field(session)
    if not field:
        return
    prompt = QUESTION_BY_FIELD.get(field, f"Please provide `{field}`.")
    if session.mode == "update":
        prompt = f"Update request: {prompt}"
    client.chat_postMessage(channel=session.channel_id, thread_ts=session.thread_ts, text=prompt)


def _capture_field_answer(session: IntakeSession, *, field: str, event: dict[str, Any]) -> tuple[bool, str]:
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
            session.answers["repo"] = ""
            return True, "Repo left unspecified."
        session.answers["repo"] = text.splitlines()[0].strip()
        session.asked_fields.add("repo")
        return True, "Captured repo/project."

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

    mode = str(spec.get("implementation_mode", "new_feature")).strip() or "new_feature"
    spec["implementation_mode"] = mode
    spec["source_repos"] = [str(x).strip() for x in (spec.get("source_repos") or []) if str(x).strip()]
    spec["links"] = [str(x).strip() for x in (spec.get("links") or []) if str(x).strip()]
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


def _start_create_intake(client: Any, settings: Any, *, channel_id: str, user_id: str, seed_title: str) -> None:
    msg = client.chat_postMessage(
        channel=channel_id,
        text=f"Feature request intake started by <@{user_id}>. Reply in this thread.",
    )
    thread_ts = msg["ts"]

    answers: dict[str, Any] = {}
    if seed_title:
        answers["title"] = seed_title[:200]

    session = IntakeSession(
        mode="create",
        feature_id="",
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_ts=thread_ts,
        queue=_build_create_queue(has_title=bool(seed_title)),
        answers=answers,
    )
    if seed_title:
        session.asked_fields.add("title")

    _store_session(session)

    if seed_title:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"Captured request title: *{seed_title[:200]}*",
        )

    client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=(
            "I will ask 2-3 short questions in this thread and then start the build automatically.\n"
            "If I stop responding after your reply, update Slack app scopes/events per `docs/SETUP_SLACK.md` and reinstall."
        ),
    )

    _ask_next_question(client, session)


def _start_update_intake(client: Any, settings: Any, *, feature: dict[str, Any], channel_id: str, thread_ts: str, user_id: str, message_ts: str) -> None:
    spec = feature.get("spec") or {}
    queue = _build_update_queue(feature)
    answers: dict[str, Any] = {}
    if "links" in queue:
        answers["links"] = [str(x).strip() for x in (spec.get("links") or []) if str(x).strip()]

    session = IntakeSession(
        mode="update",
        feature_id=str(feature.get("id") or ""),
        user_id=user_id,
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
        return False, f"Failed to contact build endpoint: `{e}`", None

    if r.status_code >= 400:
        detail_text = ""
        try:
            payload = r.json()
            detail = payload.get("detail")
            if isinstance(detail, dict):
                missing = [str(x).strip() for x in (detail.get("missing") or []) if str(x).strip()]
                base_message = str(detail.get("message") or "").strip()
                next_action = str(detail.get("next_action") or "").strip()
                install_url = str(detail.get("install_url") or "").strip()
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
        return False, f"Build was not accepted: {detail_text}", None

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


def _finalize_create_session(client: Any, settings: Any, session: IntakeSession) -> None:
    spec = _create_spec_from_session(session)
    title = str(spec.get("title") or "").strip() or "(untitled feature)"

    payload = {
        "spec": spec,
        "requester_user_id": session.user_id,
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
            f"Created request `{feature['id']}` with status `{feature['status']}`.\n"
            f"Mode: {_format_mode(str(spec.get('implementation_mode', 'new_feature')))}"
        ),
    )
    if feature.get("status") == "NEEDS_INFO":
        _post_clarification_prompt(client, session.channel_id, session.thread_ts, feature)
        return

    if feature.get("status") == "READY_FOR_BUILD":
        ok, note, _payload = _enqueue_build_for_feature(
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
            client.chat_postMessage(
                channel=session.channel_id,
                thread_ts=session.thread_ts,
                text="Use *Add details in chat* to fix missing fields, then run build again.",
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

    app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret or "")
    cached_bot_user_id = ""

    def _resolve_bot_user_id(client: Any) -> str:
        nonlocal cached_bot_user_id
        if cached_bot_user_id:
            return cached_bot_user_id
        try:
            payload = client.auth_test()
            cached_bot_user_id = str(payload.get("user_id") or "").strip()
        except Exception:
            cached_bot_user_id = ""
        return cached_bot_user_id

    @app.event("member_joined_channel")
    def handle_member_joined_channel(event, client, logger):
        channel_id = str(event.get("channel") or "").strip()
        joined_user_id = str(event.get("user") or "").strip()
        inviter_id = str(event.get("inviter") or "").strip()
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
            logger=logger,
        )

    @app.command("/feature")
    def handle_feature(ack, body, client, logger):
        ack()
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
        _start_create_intake(client, settings, channel_id=channel_id, user_id=user_id, seed_title=text)

    @app.event("message")
    def handle_message_events(event, client, logger):
        subtype = event.get("subtype")
        if subtype in {"bot_message", "message_changed", "message_deleted", "channel_join", "channel_leave"}:
            return
        if event.get("bot_id"):
            return

        user_id = str(event.get("user") or "").strip()
        channel_id = str(event.get("channel") or "").strip()
        thread_ts = str(event.get("thread_ts") or "").strip()
        text = str(event.get("text") or "").strip()

        if not user_id or not channel_id or not thread_ts:
            return

        session = _get_session(channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
        if not session:
            logger.debug(
                "slack_message_event_no_session channel=%s thread=%s user=%s subtype=%s",
                channel_id,
                thread_ts,
                user_id,
                subtype,
            )
            return

        logger.info(
            "slack_message_event_matched_session channel=%s thread=%s user=%s field=%s subtype=%s",
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
                text="Intake cancelled. Use `/feature` to start again.",
            )
            return

        field = _next_field(session)
        if not field:
            _drop_session(session)
            return

        ok, note = _capture_field_answer(session, field=field, event=event)
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

        ok, note, _payload = _enqueue_build_for_feature(
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
