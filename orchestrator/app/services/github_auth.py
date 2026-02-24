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
        self._cached_by_installation: dict[str, tuple[str, datetime]] = {}

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

    def _app_headers(self) -> dict[str, str]:
        jwt_token = self._mint_app_jwt()
        return {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _fetch_installation_token(self, installation_id: str) -> tuple[str, datetime]:
        normalized_installation_id = (installation_id or "").strip()
        if not normalized_installation_id:
            raise GitHubAuthError("installation id is required when requesting GitHub App token")
        api_base = self.settings.github_api_base.rstrip("/")
        url = f"{api_base}/app/installations/{normalized_installation_id}/access_tokens"

        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=self._app_headers(), json={})
            response.raise_for_status()
            data = response.json()

        token = str(data.get("token", "")).strip()
        expires_at = _parse_expiry(str(data.get("expires_at", "")).strip())
        if not token:
            raise GitHubAuthError("GitHub App installation token response missing token")
        return token, expires_at

    def resolve_installation_id_for_repo(self, *, owner: str, repo: str) -> str:
        normalized_owner = (owner or "").strip()
        normalized_repo = (repo or "").strip()
        if not normalized_owner or not normalized_repo:
            raise GitHubAuthError("owner/repo is required for dynamic GitHub App installation lookup")

        api_base = self.settings.github_api_base.rstrip("/")
        url = f"{api_base}/repos/{normalized_owner}/{normalized_repo}/installation"

        with httpx.Client(timeout=30) as client:
            response = client.get(url, headers=self._app_headers())
        if response.status_code == 404:
            install_url = self.settings.github_app_install_url_resolved()
            message = f"GitHub App is not installed on repo {normalized_owner}/{normalized_repo}."
            if install_url:
                message += f" Install it here: {install_url}"
            raise GitHubAuthError(message)
        response.raise_for_status()
        payload = response.json()
        installation_id = str(payload.get("id") or "").strip()
        if not installation_id:
            raise GitHubAuthError(
                f"GitHub App installation lookup returned no installation id for {normalized_owner}/{normalized_repo}"
            )
        return installation_id

    def _get_installation_token(self, installation_id: str) -> str:
        normalized_installation_id = (installation_id or "").strip()
        if not normalized_installation_id:
            raise GitHubAuthError("GitHub App installation id is required")

        now = datetime.now(tz=timezone.utc)
        cached = self._cached_by_installation.get(normalized_installation_id)
        if cached and now < (cached[1] - timedelta(seconds=60)):
            return cached[0]

        with self._lock:
            now = datetime.now(tz=timezone.utc)
            cached = self._cached_by_installation.get(normalized_installation_id)
            if cached and now < (cached[1] - timedelta(seconds=60)):
                return cached[0]
            token, expires_at = self._fetch_installation_token(normalized_installation_id)
            self._cached_by_installation[normalized_installation_id] = (token, expires_at)
            return token

    def get_token(self, *, owner: str = "", repo: str = "") -> str:
        mode = self._mode()
        if mode in {"", "token", "pat"}:
            return self._token_from_pat()
        if mode != "app":
            raise GitHubAuthError(f"Unsupported GITHUB_AUTH_MODE '{self.settings.github_auth_mode}'")

        configured_installation_id = (self.settings.github_app_installation_id or "").strip()
        if configured_installation_id:
            return self._get_installation_token(configured_installation_id)

        normalized_owner = (owner or "").strip()
        normalized_repo = (repo or "").strip()
        if not normalized_owner or not normalized_repo:
            raise GitHubAuthError(
                "GITHUB_AUTH_MODE=app requires either GITHUB_APP_INSTALLATION_ID or a target repo "
                "(owner/repo) to resolve installation dynamically."
            )
        installation_id = self.resolve_installation_id_for_repo(owner=normalized_owner, repo=normalized_repo)
        return self._get_installation_token(installation_id)


@lru_cache
def get_github_token_provider() -> GitHubTokenProvider:
    return GitHubTokenProvider(get_settings())
