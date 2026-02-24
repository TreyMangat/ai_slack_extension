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
