from __future__ import annotations

import pytest

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
    assert runtime["auth_mode"] == "disabled"
