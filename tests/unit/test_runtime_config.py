from __future__ import annotations

import pytest

import app.config as config_mod
from app.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_validate_startup_prerequisites_rejects_missing_github_app_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("GITHUB_ENABLED", "true")
    monkeypatch.setenv("GITHUB_AUTH_MODE", "app")
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "/run/secrets/does-not-exist.pem")

    settings = Settings()
    with pytest.raises(RuntimeError):
        settings.validate_startup_prerequisites()


def test_runtime_diagnostics_reports_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("CODERUNNER_MODE", "opencode")
    monkeypatch.setenv("AUTH_MODE", "disabled")

    settings = Settings()
    runtime = settings.runtime_diagnostics()
    assert runtime["mock_mode"] is False
    assert runtime["coderunner_mode"] == "opencode"
    assert runtime["opencode_execution_mode"] == "local_openclaw"
    assert runtime["auth_mode"] == "disabled"


def test_validate_startup_prerequisites_rejects_missing_openclaw_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("CODERUNNER_MODE", "opencode")
    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "local_openclaw")
    monkeypatch.setenv("OPENCLAW_AUTH_DIR", "/tmp/does-not-exist-openclaw-auth")
    monkeypatch.setattr(config_mod.shutil, "which", lambda _: "/usr/bin/openclaw")

    settings = Settings()
    with pytest.raises(RuntimeError):
        settings.validate_startup_prerequisites()


def test_github_app_install_url_resolved_from_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("GITHUB_APP_SLUG", "feature-factory-bot")
    settings = Settings()
    assert settings.github_app_install_url_resolved() == "https://github.com/apps/feature-factory-bot/installations/new"


def test_slack_oauth_urls_resolve_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "https://example.modal.run")
    monkeypatch.setenv("SLACK_OAUTH_CALLBACK_PATH", "/api/slack/oauth/callback")
    monkeypatch.setenv("SLACK_OAUTH_INSTALL_PATH", "/api/slack/install")
    settings = Settings()
    assert settings.slack_oauth_redirect_uri_resolved() == "https://example.modal.run/api/slack/oauth/callback"
    assert settings.slack_oauth_install_url_resolved() == "https://example.modal.run/api/slack/install"


def test_validate_startup_prerequisites_allows_slack_oauth_without_static_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("GITHUB_ENABLED", "false")
    monkeypatch.setenv("ENABLE_SLACK_BOT", "true")
    monkeypatch.setenv("SLACK_MODE", "http")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "signing-secret")
    monkeypatch.setenv("ENABLE_SLACK_OAUTH", "true")
    monkeypatch.setenv("SLACK_CLIENT_ID", "123.456")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "client-secret")

    settings = Settings()
    settings.validate_startup_prerequisites()


def test_slack_app_redirect_url_with_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SLACK_APP_ID", "A123456")
    monkeypatch.setenv("SLACK_TEAM_ID", "T123456")
    settings = Settings()
    assert settings.slack_app_redirect_url_resolved() == "https://slack.com/app_redirect?app=A123456&team=T123456"


def test_github_oauth_urls_resolve_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("BASE_URL", "https://example.modal.run")
    monkeypatch.setenv("GITHUB_OAUTH_INSTALL_PATH", "/api/github/install")
    monkeypatch.setenv("GITHUB_OAUTH_CALLBACK_PATH", "/api/github/oauth/callback")
    settings = Settings()
    assert settings.github_oauth_redirect_uri_resolved() == "https://example.modal.run/api/github/oauth/callback"
    assert settings.github_oauth_install_url_resolved() == "https://example.modal.run/api/github/install"


def test_validate_startup_prerequisites_rejects_missing_github_oauth_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://feature:feature@db:5432/feature_factory")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ENABLE_GITHUB_USER_OAUTH", "true")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "abc123")
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    settings = Settings()
    with pytest.raises(RuntimeError):
        settings.validate_startup_prerequisites()
