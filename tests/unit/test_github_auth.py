from __future__ import annotations

import pytest

from app.config import Settings
import app.services.github_auth as github_auth_mod
from app.services.github_auth import GitHubAuthError, GitHubTokenProvider


def test_get_token_requires_repo_or_installation_id_for_app_mode() -> None:
    settings = Settings.model_construct(
        github_auth_mode="app",
        github_app_id="123",
        github_app_installation_id="",
        github_app_private_key="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        github_api_base="https://api.github.com",
    )
    provider = GitHubTokenProvider(settings)

    with pytest.raises(GitHubAuthError):
        provider.get_token()


def test_load_private_key_normalizes_escaped_newlines() -> None:
    settings = Settings.model_construct(
        github_auth_mode="app",
        github_app_private_key='"-----BEGIN RSA PRIVATE KEY-----\\nabc\\n-----END RSA PRIVATE KEY-----"',
        github_app_private_key_path="",
    )
    provider = GitHubTokenProvider(settings)

    assert provider._load_private_key() == "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"  # noqa: SLF001


def test_load_private_key_normalizes_legacy_backslash_newline_form() -> None:
    settings = Settings.model_construct(
        github_auth_mode="app",
        github_app_private_key="-----BEGIN RSA PRIVATE KEY-----\\\nabc\\\n-----END RSA PRIVATE KEY-----",
        github_app_private_key_path="",
    )
    provider = GitHubTokenProvider(settings)

    assert provider._load_private_key() == "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"  # noqa: SLF001


def test_get_token_prefers_connected_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.model_construct(
        enable_github_user_oauth=True,
        github_user_oauth_required=True,
        github_oauth_client_id="cid",
        github_oauth_client_secret="secret",
        github_auth_mode="app",
    )
    provider = GitHubTokenProvider(settings)

    monkeypatch.setattr(github_auth_mod, "resolve_github_user_access_token", lambda **_: "user-token-123")

    assert provider.get_token(owner="acme", repo="widgets", actor_id="U123", team_id="T123") == "user-token-123"


def test_get_token_raises_when_user_token_required_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.model_construct(
        enable_github_user_oauth=True,
        github_user_oauth_required=True,
        github_oauth_client_id="cid",
        github_oauth_client_secret="secret",
        github_auth_mode="app",
    )
    provider = GitHubTokenProvider(settings)

    monkeypatch.setattr(github_auth_mod, "resolve_github_user_access_token", lambda **_: "")
    monkeypatch.setattr(
        github_auth_mod,
        "build_github_user_connect_url",
        lambda **_: "https://example.com/connect",
    )

    with pytest.raises(GitHubAuthError) as exc:
        provider.get_token(owner="acme", repo="widgets", actor_id="U123", team_id="T123")
    assert "https://example.com/connect" in str(exc.value)
