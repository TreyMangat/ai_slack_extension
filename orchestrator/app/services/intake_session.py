"""Session management for Slack intake flows.

Extracted from slackbot.py — handles IntakeSession lifecycle, DB
persistence, and field queue management.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from rich.console import Console
from sqlalchemy import delete

from app.db import db_session
from app.models import SlackIntakeSession

console = Console()
logger = logging.getLogger("feature_factory.intake_session")

SESSION_TTL_SECONDS = 2 * 60 * 60

INTAKE_MODE_NORMAL = "normal"
INTAKE_MODE_DEVELOPER = "developer"


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


def session_key(*, team_id: str, channel_id: str, thread_ts: str, user_id: str) -> str:
    return f"{team_id}:{channel_id}:{thread_ts}:{user_id}"


def cleanup_expired_sessions() -> None:
    now = time.time()
    expired = [k for k, s in ACTIVE_INTAKES.items() if now - s.started_at > SESSION_TTL_SECONDS]
    for key in expired:
        ACTIVE_INTAKES.pop(key, None)
    try:
        with db_session() as db:
            db.execute(delete(SlackIntakeSession).where(SlackIntakeSession.expires_at <= datetime.now(timezone.utc)))
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]slack intake DB cleanup failed: {e}[/yellow]")


def session_from_record(record: SlackIntakeSession) -> IntakeSession:
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


def store_session(session: IntakeSession) -> None:
    cleanup_expired_sessions()
    key = session_key(
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


def get_session(*, team_id: str, channel_id: str, thread_ts: str, user_id: str) -> IntakeSession | None:
    cleanup_expired_sessions()
    key = session_key(team_id=team_id, channel_id=channel_id, thread_ts=thread_ts, user_id=user_id)
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
            session = session_from_record(record)
            ACTIVE_INTAKES[key] = session
            return session
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]slack intake DB read failed: {e}[/yellow]")
        return None


def drop_session(session: IntakeSession) -> None:
    key = session_key(
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


def next_field(session: IntakeSession) -> str:
    from app.services.intake_helpers import normalize_intake_mode
    is_model_flow = str(session.answers.get("_flow") or "").strip() == "model"
    while session.queue:
        current = session.queue[0]
        if current == "base_branch":
            repo_value = str(session.answers.get("repo") or session.base_spec.get("repo") or "").strip()
            if is_model_flow and repo_value:
                pass  # Model flow: keep base_branch when repo is set
            elif normalize_intake_mode(str((session.answers or {}).get("_intake_mode") or INTAKE_MODE_NORMAL)) != INTAKE_MODE_DEVELOPER or not repo_value:
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


def drop_field_from_queue(session: IntakeSession, field: str) -> None:
    session.queue = [item for item in session.queue if item != field]


def build_create_queue(*, has_title: bool, require_repo: bool, minimal: bool = True) -> list[str]:
    from app.services.intake_helpers import CREATE_FLOW_FIELDS_MINIMAL, CREATE_FLOW_FIELDS_FULL
    queue = list(CREATE_FLOW_FIELDS_MINIMAL if minimal else CREATE_FLOW_FIELDS_FULL)
    if has_title:
        queue.remove("title")
    if not require_repo and "repo" in queue:
        queue.remove("repo")
    if "repo" not in queue and "base_branch" in queue:
        queue.remove("base_branch")
    return queue


def build_update_queue(feature: dict[str, Any]) -> list[str]:
    from app.services.intake_helpers import UPDATE_FALLBACK_FIELDS
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
