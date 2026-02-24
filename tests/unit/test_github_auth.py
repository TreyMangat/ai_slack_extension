from __future__ import annotations

import pytest

from app.config import Settings
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
