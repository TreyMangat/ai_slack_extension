"""Mini-model intake router for Slack bot conversational field extraction.

Calls the MINI tier via OpenRouter to classify user messages and extract
structured fields from conversational intake threads.  The Slack bot handler
(owned by Codex) calls ``classify_intake_message`` and acts on the returned
``IntakeAction``.

Now includes:
- Rich, context-aware system prompts via ``intake_prompts``
- Repo/branch awareness from Repo Indexer or GitHub adapter
- User skill detection (developer / technical / non_technical)
- Mid-conversation escalation to the FRONTIER model
- User history injection for returning requesters
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.config import get_settings
from app.services.intake_prompts import build_intake_system_prompt

logger = logging.getLogger(__name__)

_CANCEL_KEYWORDS = frozenset({"cancel", "stop", "quit", "nevermind", "never mind"})


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class IntakeAction(BaseModel):
    action: Literal["ask_field", "confirm", "clarify", "cancel", "escalate"] = "clarify"
    fields: dict[str, Any] = {}
    # Legacy single-field (backward compat with older prompts/tests):
    field_name: Optional[str] = None
    field_value: Optional[str] = None
    next_question: Optional[str] = None
    confidence: float = 0.0
    reasoning: str = ""
    user_skill: Literal["developer", "technical", "non_technical"] = "technical"
    suggested_repo: Optional[str] = None
    suggested_branch: Optional[str] = None


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

async def _gather_intake_context(slack_user_id: str | None = None) -> dict[str, Any]:
    """Fetch repos/branches from Repo Indexer and/or GitHub for prompt injection."""
    context: dict[str, Any] = {
        "repos": None,
        "branches": None,
        "user_history": None,
        "github_status": None,
    }

    # Check GitHub connection FIRST (when we have a user)
    if slack_user_id:
        try:
            from app.services.github_connection import check_github_connection

            context["github_status"] = await check_github_connection(slack_user_id)
        except Exception:  # noqa: BLE001
            logger.debug("GitHub connection check failed")

    # Only fetch repos if GitHub is connected (or no status available)
    _should_fetch_repos = True
    gh_status = context.get("github_status")
    if gh_status is not None and not gh_status.repos_available:
        _should_fetch_repos = False

    if _should_fetch_repos:
        # Try Repo Indexer first (has ranked search)
        try:
            from app.services.repo_indexer_adapter import get_repo_indexer_client

            client = get_repo_indexer_client()
            if client:
                actor_id = slack_user_id or ""
                catalog = client.suggest_repos_and_branches(actor_id=actor_id)
                if isinstance(catalog, dict):
                    repos_raw = catalog.get("repos") or catalog.get("repositories") or []
                    if isinstance(repos_raw, list) and repos_raw:
                        context["repos"] = repos_raw
                    branches_raw = catalog.get("branches") or {}
                    if isinstance(branches_raw, dict) and branches_raw:
                        context["branches"] = branches_raw
        except Exception:  # noqa: BLE001
            logger.debug("Repo Indexer unavailable for intake context")

        # Fallback: try GitHub adapter for repo list
        if not context["repos"]:
            try:
                from app.services.github_adapter import get_github_adapter

                settings = get_settings()
                gh = get_github_adapter(
                    owner=settings.github_repo_owner,
                    repo=settings.github_repo_name,
                    strict=False,
                )
                if hasattr(gh, "list_repos"):
                    repos = gh.list_repos()
                    if isinstance(repos, list) and repos:
                        context["repos"] = repos
            except Exception:  # noqa: BLE001
                logger.debug("GitHub adapter unavailable for intake context")

    # User history
    if slack_user_id:
        try:
            context["user_history"] = await _get_user_history(slack_user_id)
        except Exception:  # noqa: BLE001
            logger.debug("Could not fetch user history")

    return context


async def _get_user_history(slack_user_id: str, db: Any = None) -> list[dict[str, Any]]:
    """Fetch the user's last 5 feature requests for context.

    Returns a list of dicts with title, repo, status, created_at.
    Never raises — returns empty list on any error.
    """
    try:
        from sqlalchemy import select

        from app.db import db_session
        from app.models import FeatureRequest

        with db_session() as session:
            stmt = (
                select(FeatureRequest)
                .where(FeatureRequest.requester_user_id == slack_user_id)
                .order_by(FeatureRequest.created_at.desc())
                .limit(5)
            )
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "title": row.title,
                    "repo": str((row.spec or {}).get("repo", "")),
                    "status": row.status,
                    "created_at": str(row.created_at),
                }
                for row in rows
            ]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Frontier escalation
# ---------------------------------------------------------------------------

_ESCALATION_SYSTEM_PROMPT_PREFIX = (
    "You are a senior product architect reviewing a feature request that the "
    "initial intake assistant could not fully handle. Provide a thorough analysis. "
    "Escalation reason: "
)


async def escalate_to_frontier(
    message: str,
    conversation_history: list[dict[str, Any]],
    current_fields: dict[str, Any],
    escalation_reason: str = "Escalated from mini model",
    *,
    slack_user_id: str | None = None,
) -> IntakeAction:
    """Send the full context to the frontier model for sophisticated analysis.

    Called when the mini model cannot handle the request (multi-repo,
    architectural, or after repeated clarification attempts).
    Falls back to a clarify action on any failure.

    Parameters
    ----------
    slack_user_id : str, optional
        Accepted for call-signature compatibility with the slackbot sync
        wrapper but not used directly (context was already gathered by the
        mini-model path).
    """
    user_prompt = _build_user_prompt(message, conversation_history, current_fields)
    system_prompt = (
        _ESCALATION_SYSTEM_PROMPT_PREFIX + escalation_reason + "\n\n"
        + build_intake_system_prompt()
    )

    try:
        from app.services.openrouter_provider import ModelTier, call_openrouter

        response = await call_openrouter(
            prompt=user_prompt,
            tier=ModelTier.FRONTIER,
            system_prompt=system_prompt,
            response_format="json_object",
        )
        parsed = json.loads(response.content) if isinstance(response.content, str) else response.content
        return IntakeAction(**{k: v for k, v in parsed.items() if k in IntakeAction.model_fields})
    except Exception as exc:  # noqa: BLE001
        logger.warning("intake_router: frontier escalation failed: %s", exc)
        return IntakeAction(
            action="clarify",
            next_question=(
                "This request seems complex. Could you break it down a bit? "
                "What's the single most important thing you need built first?"
            ),
            confidence=0.0,
            reasoning=f"Frontier escalation failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def classify_intake_message(
    message: str,
    conversation_history: list[dict[str, Any]],
    current_fields: dict[str, Any],
    slack_user_id: str | None = None,
) -> IntakeAction:
    """Classify an incoming Slack message and return the next action.

    Short-circuits for cancel keywords without calling the LLM.
    Falls back to a safe "clarify" action on any provider error.

    Parameters
    ----------
    slack_user_id : str, optional
        Slack user ID for fetching user history and repo suggestions.
        Callers that don't have this can omit it — fully backward-compatible.
    """

    # ---- cancel keyword short-circuit (no LLM call) ----
    normalized = message.strip().lower()
    if normalized in _CANCEL_KEYWORDS:
        return IntakeAction(
            action="cancel",
            confidence=1.0,
            reasoning="User sent cancel keyword",
        )

    # ---- gather context (repos, branches, history) ----
    context = await _gather_intake_context(slack_user_id)

    # ---- build prompts ----
    system_prompt = build_intake_system_prompt(
        available_repos=context.get("repos"),
        available_branches=context.get("branches"),
        user_history=context.get("user_history"),
        github_status=context.get("github_status"),
    )
    user_prompt = _build_user_prompt(message, conversation_history, current_fields)

    # ---- call MINI tier via OpenRouter ----
    try:
        from app.services.openrouter_provider import (
            ModelTier,
            call_openrouter,
        )

        response = await call_openrouter(
            prompt=user_prompt,
            tier=ModelTier.MINI,
            system_prompt=system_prompt,
            response_format="json_object",
        )
        raw = response.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("intake_router: OpenRouter call failed, returning clarify fallback: %s", exc)
        return IntakeAction(
            action="clarify",
            next_question="Sorry, could you rephrase that?",
            confidence=0.0,
            reasoning=f"OpenRouter error: {exc}",
        )

    # ---- parse response into IntakeAction ----
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        action = IntakeAction(**{k: v for k, v in parsed.items() if k in IntakeAction.model_fields})
    except Exception as exc:  # noqa: BLE001
        logger.warning("intake_router: failed to parse LLM response: %s", exc)
        return IntakeAction(
            action="clarify",
            next_question="Sorry, could you rephrase that?",
            confidence=0.0,
            reasoning=f"Parse error: {exc}",
        )

    # ---- special field handling (github_reauth / github_connect) ----
    _SPECIAL_FIELDS = {"github_reauth", "github_connect"}
    if action.field_name in _SPECIAL_FIELDS:
        action.field_value = None  # Never store these as feature data

    # ---- confidence gate ----
    if action.confidence < 0.6 and action.action not in ("cancel", "escalate"):
        action.action = "clarify"
        if not action.next_question:
            action.next_question = "I'm not fully sure I understood. Could you give me a bit more detail?"

    # ---- auto-escalate if mini says so ----
    if action.action == "escalate":
        return await escalate_to_frontier(
            message,
            conversation_history,
            current_fields,
            escalation_reason=action.reasoning or "Mini model requested escalation",
        )

    return action


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_prompt(
    message: str,
    conversation_history: list[dict[str, Any]],
    current_fields: dict[str, Any],
) -> str:
    parts: list[str] = []

    if current_fields:
        parts.append("Fields collected so far:")
        for k, v in current_fields.items():
            parts.append(f"  {k}: {v}")
        parts.append("")

    if conversation_history:
        parts.append("Conversation history:")
        for entry in conversation_history[-10:]:  # keep context manageable
            role = entry.get("role", "user")
            text = entry.get("text", entry.get("content", ""))
            parts.append(f"  [{role}] {text}")
        parts.append("")

    parts.append(f"Latest message from user: {message}")
    return "\n".join(parts)
