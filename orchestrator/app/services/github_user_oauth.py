from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import desc, or_, select

from app.config import get_settings
from app.db import db_session
from app.models import GitHubUserConnection


GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"


@dataclass
class GitHubUserOAuthCallbackResult:
    slack_user_id: str
    slack_team_id: str
    github_login: str
    github_user_id: str
    next_url: str


def _urlsafe_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _state_secret() -> bytes:
    settings = get_settings()
    key = (settings.secret_key or "").strip()
    if not key:
        raise RuntimeError("SECRET_KEY is required for GitHub OAuth state signing.")
    return key.encode("utf-8")


def _encode_state(payload: dict[str, str]) -> str:
    encoded = _urlsafe_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_state_secret(), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_state(raw_state: str) -> dict[str, str]:
    state_text = (raw_state or "").strip()
    if "." not in state_text:
        raise RuntimeError("Invalid GitHub OAuth state.")
    encoded, signature = state_text.rsplit(".", 1)
    expected = hmac.new(_state_secret(), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RuntimeError("GitHub OAuth state signature mismatch.")

    payload_raw = _urlsafe_decode(encoded)
    parsed = json.loads(payload_raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError("Invalid GitHub OAuth state payload.")
    payload = {str(k): str(v) for k, v in parsed.items()}

    settings = get_settings()
    issued_at = int(payload.get("iat") or "0")
    max_age_seconds = max(int(settings.github_oauth_state_expiration_seconds), 60)
    now = int(time.time())
    if not issued_at or abs(now - issued_at) > max_age_seconds:
        raise RuntimeError("GitHub OAuth state expired.")
    return payload


def _fernet_for_user_tokens() -> Fernet:
    settings = get_settings()
    raw_key = (settings.github_user_token_encryption_key or "").strip()
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception:
            seed = hashlib.sha256(raw_key.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(seed))
    seed = hashlib.sha256((settings.secret_key or "").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(seed))


def _encrypt_user_token(token: str) -> str:
    value = (token or "").strip()
    if not value:
        return ""
    return _fernet_for_user_tokens().encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt_user_token(token_encrypted: str) -> str:
    value = (token_encrypted or "").strip()
    if not value:
        return ""
    try:
        return _fernet_for_user_tokens().decrypt(value.encode("utf-8")).decode("utf-8").strip()
    except Exception:
        return ""


def build_github_user_connect_url(*, slack_user_id: str, slack_team_id: str = "", next_url: str = "") -> str:
    settings = get_settings()
    return settings.github_oauth_install_url_for_user(
        slack_user_id=(slack_user_id or "").strip(),
        slack_team_id=(slack_team_id or "").strip(),
        next_url=(next_url or "").strip(),
    )


def build_github_oauth_authorize_url(*, slack_user_id: str, slack_team_id: str = "", next_url: str = "") -> str:
    settings = get_settings()
    if not settings.github_user_oauth_enabled():
        raise RuntimeError("GitHub user OAuth is disabled.")
    user_id = (slack_user_id or "").strip()
    if not user_id:
        raise RuntimeError("Missing required slack_user_id.")
    team_id = (slack_team_id or "").strip()
    redirect_uri = settings.github_oauth_redirect_uri_resolved()
    if not redirect_uri:
        raise RuntimeError("BASE_URL must be configured for GitHub OAuth redirect URI.")

    state_payload = {
        "slack_user_id": user_id,
        "slack_team_id": team_id,
        "next": (next_url or "").strip(),
        "iat": str(int(time.time())),
        "nonce": secrets.token_urlsafe(12),
    }
    state = _encode_state(state_payload)

    params = {
        "client_id": (settings.github_oauth_client_id or "").strip(),
        "redirect_uri": redirect_uri,
        "scope": " ".join(settings.github_oauth_scopes_list()),
        "state": state,
    }
    return f"{GITHUB_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def _exchange_oauth_code_for_token(*, code: str) -> tuple[str, str, str]:
    settings = get_settings()
    payload = {
        "client_id": (settings.github_oauth_client_id or "").strip(),
        "client_secret": (settings.github_oauth_client_secret or "").strip(),
        "code": (code or "").strip(),
        "redirect_uri": settings.github_oauth_redirect_uri_resolved(),
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(
            GITHUB_OAUTH_TOKEN_URL,
            data=payload,
            headers={"Accept": "application/json"},
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("GitHub OAuth token exchange returned an invalid response.")
    if str(data.get("error") or "").strip():
        raise RuntimeError(str(data.get("error_description") or data.get("error") or "OAuth token exchange failed"))

    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("GitHub OAuth token exchange returned no access token.")
    token_scope = str(data.get("scope") or "").strip()
    token_type = str(data.get("token_type") or "").strip() or "bearer"
    return access_token, token_scope, token_type


def _fetch_github_user_identity(*, access_token: str) -> tuple[str, str]:
    settings = get_settings()
    with httpx.Client(timeout=30) as client:
        response = client.get(
            f"{settings.github_api_base.rstrip('/')}/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("GitHub user lookup returned an invalid response.")
    github_login = str(data.get("login") or "").strip()
    github_user_id = str(data.get("id") or "").strip()
    if not github_login:
        raise RuntimeError("GitHub user lookup returned no login.")
    return github_login, github_user_id


def _upsert_user_connection(
    *,
    slack_user_id: str,
    slack_team_id: str,
    github_login: str,
    github_user_id: str,
    access_token: str,
    token_scope: str,
    token_type: str,
) -> None:
    with db_session() as db:
        stmt = (
            select(GitHubUserConnection)
            .where(GitHubUserConnection.slack_user_id == slack_user_id)
            .where(GitHubUserConnection.slack_team_id == slack_team_id)
            .limit(1)
        )
        connection = db.execute(stmt).scalars().first()
        if not connection:
            connection = GitHubUserConnection(
                slack_user_id=slack_user_id,
                slack_team_id=slack_team_id,
            )
            db.add(connection)
        connection.github_login = github_login
        connection.github_user_id = github_user_id
        connection.access_token_encrypted = _encrypt_user_token(access_token)
        connection.token_scope = (token_scope or "").strip()
        connection.token_type = (token_type or "").strip()
        connection.last_used_at = datetime.utcnow()


def complete_github_oauth_callback(*, code: str, state: str) -> GitHubUserOAuthCallbackResult:
    oauth_code = (code or "").strip()
    if not oauth_code:
        raise RuntimeError("Missing GitHub OAuth code.")
    payload = _decode_state(state)
    slack_user_id = str(payload.get("slack_user_id") or "").strip()
    slack_team_id = str(payload.get("slack_team_id") or "").strip()
    next_url = str(payload.get("next") or "").strip()
    if not slack_user_id:
        raise RuntimeError("Missing Slack user mapping in OAuth state.")

    access_token, token_scope, token_type = _exchange_oauth_code_for_token(code=oauth_code)
    github_login, github_user_id = _fetch_github_user_identity(access_token=access_token)
    _upsert_user_connection(
        slack_user_id=slack_user_id,
        slack_team_id=slack_team_id,
        github_login=github_login,
        github_user_id=github_user_id,
        access_token=access_token,
        token_scope=token_scope,
        token_type=token_type,
    )

    return GitHubUserOAuthCallbackResult(
        slack_user_id=slack_user_id,
        slack_team_id=slack_team_id,
        github_login=github_login,
        github_user_id=github_user_id,
        next_url=next_url,
    )


def resolve_github_user_access_token(*, slack_user_id: str, slack_team_id: str = "") -> str:
    user_id = (slack_user_id or "").strip()
    team_id = (slack_team_id or "").strip()
    if not user_id:
        return ""

    with db_session() as db:
        stmt = select(GitHubUserConnection).where(GitHubUserConnection.slack_user_id == user_id)
        if team_id:
            stmt = stmt.where(
                or_(
                    GitHubUserConnection.slack_team_id == team_id,
                    GitHubUserConnection.slack_team_id == "",
                )
            )
        stmt = stmt.order_by(
            desc(GitHubUserConnection.slack_team_id == team_id),
            desc(GitHubUserConnection.updated_at),
        ).limit(1)
        connection = db.execute(stmt).scalars().first()
        if not connection:
            return ""
        token = _decrypt_user_token(connection.access_token_encrypted)
        if not token:
            return ""
        connection.last_used_at = datetime.utcnow()
        return token


def has_github_user_connection(*, slack_user_id: str, slack_team_id: str = "") -> bool:
    user_id = (slack_user_id or "").strip()
    team_id = (slack_team_id or "").strip()
    if not user_id:
        return False

    with db_session() as db:
        stmt = select(GitHubUserConnection.id).where(GitHubUserConnection.slack_user_id == user_id)
        if team_id:
            stmt = stmt.where(
                or_(
                    GitHubUserConnection.slack_team_id == team_id,
                    GitHubUserConnection.slack_team_id == "",
                )
            )
        stmt = stmt.order_by(
            desc(GitHubUserConnection.slack_team_id == team_id),
            desc(GitHubUserConnection.updated_at),
        ).limit(1)
        return db.execute(stmt).first() is not None
