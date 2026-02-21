from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from rich.console import Console

from app.config import get_settings
from app.services.reviewer_service import is_approver_allowed


console = Console()

QUESTION_BY_FIELD: dict[str, str] = {
    "title": "What do you want to build?",
    "problem": "What problem are users facing?",
    "business_justification": "Why is this needed now?",
    "links": "Attach request if applicable: paste links or drop files in this thread. Reply `skip` if none.",
    "repo": "Do you know what project/repo this belongs to? Reply with `org/repo`, repo URL, or `unsure`.",
    "implementation_mode": "Should implementation start from scratch or reuse existing project patterns? Reply `scratch` or `reuse`.",
    "source_repos": "If reusing existing patterns, which repos should be references? One per line.",
    "proposed_solution": "Any preferred implementation approach or constraints? Reply `skip` if none.",
    "acceptance_criteria": "How will we know this is done? Share acceptance criteria, one per line.",
}

CREATE_FLOW_FIELDS = [
    "title",
    "problem",
    "business_justification",
    "links",
    "repo",
    "implementation_mode",
    "source_repos",
    "proposed_solution",
    "acceptance_criteria",
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


def _store_session(session: IntakeSession) -> None:
    _cleanup_expired_sessions()
    key = _session_key(channel_id=session.channel_id, thread_ts=session.thread_ts, user_id=session.user_id)
    ACTIVE_INTAKES[key] = session


def _get_session(*, channel_id: str, thread_ts: str, user_id: str) -> IntakeSession | None:
    _cleanup_expired_sessions()
    key = _session_key(channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
    return ACTIVE_INTAKES.get(key)


def _drop_session(session: IntakeSession) -> None:
    key = _session_key(channel_id=session.channel_id, thread_ts=session.thread_ts, user_id=session.user_id)
    ACTIVE_INTAKES.pop(key, None)


def _feature_message_blocks(feature: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    fid = feature["id"]
    status = feature["status"]
    title = feature["title"]
    spec = feature.get("spec") or {}
    mode = spec.get("implementation_mode", "new_feature")
    preview = feature.get("preview_url") or ""
    pr = feature.get("github_pr_url") or ""
    issue = feature.get("github_issue_url") or ""
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
        {
            "type": "button",
            "action_id": "ff_run_build",
            "text": {"type": "plain_text", "text": "Run build"},
            "style": "primary",
            "value": fid,
        },
        {
            "type": "button",
            "action_id": "ff_approve",
            "text": {"type": "plain_text", "text": "Approve"},
            "style": "danger",
            "value": fid,
        },
    ]

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
                        f"Issue: {issue or '(none)'} | PR: {pr or '(pending)'} | "
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
        if not criteria:
            return False, "Please provide at least one acceptance criterion."
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
            "I will ask one question at a time in this thread.\n"
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


def main() -> None:
    settings = get_settings()

    if not settings.enable_slack_bot:
        console.print("[yellow]Slack bot is disabled (ENABLE_SLACK_BOT=false). Sleeping...[/yellow]")
        while True:
            time.sleep(3600)

    if not settings.slack_bot_token or not settings.slack_app_token:
        console.print("[red]Missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN. Sleeping...[/red]")
        while True:
            time.sleep(3600)

    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret or "")

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

        try:
            headers = _api_headers(settings)
            r = httpx.post(
                f"{settings.orchestrator_internal_url}/api/feature-requests/{feature_id}/build",
                json={"actor_type": "slack", "actor_id": user_id, "message": "Build requested from Slack"},
                timeout=30,
                headers=headers,
            )
            r.raise_for_status()
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text="Build enqueued.")
        except Exception as e:  # noqa: BLE001
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Failed to enqueue build: `{e}`")

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

    console.print("[green]Starting Slack Socket Mode handler...[/green]")
    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
