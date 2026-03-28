"""GitHub OAuth connection checker for intake context.

Validates whether a Slack user's stored GitHub token is still usable
before attempting to fetch repos/branches.  Designed to be fast and
never-failing — every code path returns a ``GitHubConnectionCheck``.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)

GITHUB_USER_ENDPOINT = "/user"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class GitHubConnectionStatus(str, Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    NOT_CONNECTED = "not_connected"
    RATE_LIMITED = "rate_limited"


class GitHubConnectionCheck(BaseModel):
    status: GitHubConnectionStatus
    username: Optional[str] = None
    repos_available: bool = False
    token_created_at: Optional[str] = None
    message: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def check_github_connection(
    slack_user_id: str,
    slack_team_id: str = "",
    db: Any = None,
) -> GitHubConnectionCheck:
    """Check if a Slack user has a valid GitHub connection.

    1. Look up ``GitHubUserConnection`` for this slack_user_id
    2. If no record -> NOT_CONNECTED
    3. Decrypt the stored token and call ``GET /user`` against GitHub API
    4. If 401 -> EXPIRED
    5. If 403 + rate-limit exhausted -> RATE_LIMITED
    6. If 200 -> CONNECTED

    Never raises — returns a result with the error in ``message``.
    """
    user_id = (slack_user_id or "").strip()
    if not user_id:
        return GitHubConnectionCheck(
            status=GitHubConnectionStatus.NOT_CONNECTED,
            message="No Slack user ID provided",
        )

    # ---- look up stored connection ----
    try:
        from app.services.github_user_oauth import _decrypt_user_token

        connection = _lookup_connection(user_id, slack_team_id)
        if connection is None:
            logger.info("github_connection_check", extra={"slack_user_id": user_id, "status": "not_connected"})
            return GitHubConnectionCheck(
                status=GitHubConnectionStatus.NOT_CONNECTED,
                message="No GitHub connection found for this Slack user",
            )

        token = _decrypt_user_token(connection.get("access_token_encrypted", ""))
        if not token:
            logger.info("github_connection_check", extra={"slack_user_id": user_id, "status": "expired", "reason": "empty_token"})
            return GitHubConnectionCheck(
                status=GitHubConnectionStatus.EXPIRED,
                username=connection.get("github_login"),
                token_created_at=connection.get("created_at"),
                message="Stored GitHub token could not be decrypted",
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("github_connection_check: DB lookup failed: %s", exc)
        return GitHubConnectionCheck(
            status=GitHubConnectionStatus.NOT_CONNECTED,
            message=f"Connection lookup failed: {exc}",
        )

    # ---- validate token against GitHub API ----
    settings = get_settings()
    api_base = settings.github_api_base.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{api_base}{GITHUB_USER_ENDPOINT}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("github_connection_check", extra={"slack_user_id": user_id, "status": "not_connected", "reason": "timeout_or_network"})
        return GitHubConnectionCheck(
            status=GitHubConnectionStatus.NOT_CONNECTED,
            username=connection.get("github_login"),
            token_created_at=connection.get("created_at"),
            message=f"GitHub API unreachable: {exc}",
        )

    if resp.status_code == 200:
        data = resp.json() if isinstance(resp.json(), dict) else {}
        username = str(data.get("login") or connection.get("github_login") or "").strip()
        logger.info("github_connection_check", extra={"slack_user_id": user_id, "status": "connected", "username": username})
        return GitHubConnectionCheck(
            status=GitHubConnectionStatus.CONNECTED,
            username=username,
            repos_available=True,
            token_created_at=connection.get("created_at"),
            message=f"Connected as @{username}",
        )

    if resp.status_code == 401:
        logger.info("github_connection_check", extra={"slack_user_id": user_id, "status": "expired"})
        return GitHubConnectionCheck(
            status=GitHubConnectionStatus.EXPIRED,
            username=connection.get("github_login"),
            token_created_at=connection.get("created_at"),
            message="GitHub token has expired or been revoked",
        )

    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining", "")
        if remaining == "0":
            logger.info("github_connection_check", extra={"slack_user_id": user_id, "status": "rate_limited"})
            return GitHubConnectionCheck(
                status=GitHubConnectionStatus.RATE_LIMITED,
                username=connection.get("github_login"),
                token_created_at=connection.get("created_at"),
                message="GitHub API rate limit exceeded",
            )

    # Unexpected status — treat as not connected
    logger.info("github_connection_check", extra={"slack_user_id": user_id, "status": "not_connected", "http_status": resp.status_code})
    return GitHubConnectionCheck(
        status=GitHubConnectionStatus.NOT_CONNECTED,
        username=connection.get("github_login"),
        token_created_at=connection.get("created_at"),
        message=f"GitHub API returned unexpected status {resp.status_code}",
    )


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

async def refresh_github_token(slack_user_id: str, slack_team_id: str = "", db: Any = None) -> bool:
    """Attempt to refresh an expired GitHub token.

    GitHub OAuth tokens issued via the web flow do not include refresh
    tokens (only GitHub App user-to-server tokens do).  This function
    is best-effort: it checks for a refresh token in the connection
    record, attempts the refresh, and updates the stored token on success.

    Returns True if refresh succeeded, False if the user needs to re-auth.
    """
    user_id = (slack_user_id or "").strip()
    if not user_id:
        return False

    try:
        from app.services.github_user_oauth import _decrypt_user_token, _encrypt_user_token

        connection_row = _lookup_connection_row(user_id, slack_team_id)
        if connection_row is None:
            return False

        # GitHub web-flow tokens don't have refresh tokens, so this field
        # doesn't exist on the model today.  If it's ever added, we'd use it.
        refresh_token_encrypted = getattr(connection_row, "refresh_token_encrypted", "") or ""
        refresh_token = _decrypt_user_token(refresh_token_encrypted) if refresh_token_encrypted else ""
        if not refresh_token:
            return False

        settings = get_settings()
        payload = {
            "client_id": (settings.github_oauth_client_id or "").strip(),
            "client_secret": (settings.github_oauth_client_secret or "").strip(),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data=payload,
                headers={"Accept": "application/json"},
            )

        if resp.status_code != 200:
            return False

        data = resp.json()
        new_token = str(data.get("access_token") or "").strip()
        if not new_token:
            return False

        # Update in DB
        from app.db import db_session as _db_session

        with _db_session() as session:
            from sqlalchemy import select
            from app.models import GitHubUserConnection

            stmt = (
                select(GitHubUserConnection)
                .where(GitHubUserConnection.slack_user_id == user_id)
                .limit(1)
            )
            record = session.execute(stmt).scalars().first()
            if record:
                record.access_token_encrypted = _encrypt_user_token(new_token)
                new_refresh = str(data.get("refresh_token") or "").strip()
                if new_refresh and hasattr(record, "refresh_token_encrypted"):
                    record.refresh_token_encrypted = _encrypt_user_token(new_refresh)

        return True

    except Exception as exc:  # noqa: BLE001
        logger.debug("refresh_github_token failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lookup_connection(slack_user_id: str, slack_team_id: str = "") -> dict[str, Any] | None:
    """Look up a GitHub user connection and return it as a plain dict.

    Returns None if no connection found.  Never raises.
    """
    try:
        from sqlalchemy import select
        from sqlalchemy.sql.expression import desc

        from app.db import db_session
        from app.models import GitHubUserConnection

        with db_session() as session:
            stmt = select(GitHubUserConnection).where(
                GitHubUserConnection.slack_user_id == slack_user_id
            )
            team_id = (slack_team_id or "").strip()
            if team_id:
                from sqlalchemy import or_
                stmt = stmt.where(
                    or_(
                        GitHubUserConnection.slack_team_id == team_id,
                        GitHubUserConnection.slack_team_id == "",
                    )
                )
            stmt = stmt.order_by(desc(GitHubUserConnection.updated_at)).limit(1)
            row = session.execute(stmt).scalars().first()
            if not row:
                return None
            return {
                "github_login": row.github_login or "",
                "github_user_id": row.github_user_id or "",
                "access_token_encrypted": row.access_token_encrypted or "",
                "created_at": str(row.created_at) if row.created_at else "",
            }
    except Exception:  # noqa: BLE001
        return None


def _lookup_connection_row(slack_user_id: str, slack_team_id: str = "") -> Any:
    """Return the raw ORM row (for refresh_github_token). Returns None on failure."""
    try:
        from sqlalchemy import select
        from sqlalchemy.sql.expression import desc

        from app.db import db_session
        from app.models import GitHubUserConnection

        with db_session() as session:
            stmt = select(GitHubUserConnection).where(
                GitHubUserConnection.slack_user_id == slack_user_id
            )
            team_id = (slack_team_id or "").strip()
            if team_id:
                from sqlalchemy import or_
                stmt = stmt.where(
                    or_(
                        GitHubUserConnection.slack_team_id == team_id,
                        GitHubUserConnection.slack_team_id == "",
                    )
                )
            stmt = stmt.order_by(desc(GitHubUserConnection.updated_at)).limit(1)
            return session.execute(stmt).scalars().first()
    except Exception:  # noqa: BLE001
        return None
