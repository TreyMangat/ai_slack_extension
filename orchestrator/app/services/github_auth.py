from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import threading

import httpx
import jwt

from app.config import Settings, get_settings


class GitHubAuthError(RuntimeError):
    pass


def _parse_expiry(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class GitHubTokenProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._cached_token = ""
        self._cached_expires_at = datetime.fromtimestamp(0, tz=timezone.utc)

    def _mode(self) -> str:
        return self.settings.github_auth_mode_normalized()

    def _token_from_pat(self) -> str:
        token = (self.settings.github_token or "").strip()
        if not token:
            raise GitHubAuthError("GITHUB_TOKEN is required when GITHUB_AUTH_MODE=token")
        return token

    def _load_private_key(self) -> str:
        key_path = (self.settings.github_app_private_key_path or "").strip()
        inline_key = (self.settings.github_app_private_key or "").strip()

        if key_path:
            path = Path(key_path)
            if not path.exists():
                raise GitHubAuthError(f"GITHUB_APP_PRIVATE_KEY_PATH not found: {path}")
            return path.read_text(encoding="utf-8")

        if inline_key:
            # Supports multiline content passed through env.
            return inline_key.replace("\\n", "\n")

        raise GitHubAuthError("GitHub App auth requires GITHUB_APP_PRIVATE_KEY_PATH or GITHUB_APP_PRIVATE_KEY")

    def _mint_app_jwt(self) -> str:
        app_id = (self.settings.github_app_id or "").strip()
        if not app_id:
            raise GitHubAuthError("GITHUB_APP_ID is required when GITHUB_AUTH_MODE=app")

        private_key = self._load_private_key()
        now = datetime.now(tz=timezone.utc)
        ttl = max(min(self.settings.github_app_jwt_ttl_seconds, 540), 60)
        payload = {
            "iat": int((now - timedelta(seconds=30)).timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
            "iss": app_id,
        }
        return jwt.encode(payload, private_key, algorithm="RS256")

    def _fetch_installation_token(self) -> tuple[str, datetime]:
        installation_id = (self.settings.github_app_installation_id or "").strip()
        if not installation_id:
            raise GitHubAuthError("GITHUB_APP_INSTALLATION_ID is required when GITHUB_AUTH_MODE=app")

        api_base = self.settings.github_api_base.rstrip("/")
        jwt_token = self._mint_app_jwt()
        url = f"{api_base}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, json={})
            response.raise_for_status()
            data = response.json()

        token = str(data.get("token", "")).strip()
        expires_at = _parse_expiry(str(data.get("expires_at", "")).strip())
        if not token:
            raise GitHubAuthError("GitHub App installation token response missing token")
        return token, expires_at

    def get_token(self) -> str:
        mode = self._mode()
        if mode in {"", "token", "pat"}:
            return self._token_from_pat()
        if mode != "app":
            raise GitHubAuthError(f"Unsupported GITHUB_AUTH_MODE '{self.settings.github_auth_mode}'")

        now = datetime.now(tz=timezone.utc)
        if self._cached_token and now < (self._cached_expires_at - timedelta(seconds=60)):
            return self._cached_token

        with self._lock:
            now = datetime.now(tz=timezone.utc)
            if self._cached_token and now < (self._cached_expires_at - timedelta(seconds=60)):
                return self._cached_token
            token, expires_at = self._fetch_installation_token()
            self._cached_token = token
            self._cached_expires_at = expires_at
            return token


@lru_cache
def get_github_token_provider() -> GitHubTokenProvider:
    return GitHubTokenProvider(get_settings())
