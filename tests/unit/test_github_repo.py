from __future__ import annotations

from app.config import Settings
from app.services.github_repo import parse_repo_slug, resolve_repo_for_spec


def _settings() -> Settings:
    return Settings.model_construct(
        github_repo_owner="fallback-owner",
        github_repo_name="fallback-repo",
    )


def test_parse_repo_slug_supports_https_url() -> None:
    owner, repo = parse_repo_slug("https://github.com/acme/widgets.git")
    assert owner == "acme"
    assert repo == "widgets"


def test_resolve_repo_for_spec_prefers_spec_repo() -> None:
    settings = _settings()
    owner, repo = resolve_repo_for_spec(spec={"repo": "acme/live-repo"}, settings=settings)
    assert owner == "acme"
    assert repo == "live-repo"


def test_resolve_repo_for_spec_falls_back_to_env_repo() -> None:
    settings = _settings()
    owner, repo = resolve_repo_for_spec(spec={}, settings=settings)
    assert owner == "fallback-owner"
    assert repo == "fallback-repo"
