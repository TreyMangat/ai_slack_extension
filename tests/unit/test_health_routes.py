from __future__ import annotations

import json
import logging

from app.api.routes import health as health_mod
from app.config import Settings


class _HealthyRedis:
    def ping(self):
        return True


class _HealthyDb:
    def execute(self, _query):
        return 1


def test_health_runtime_reports_openrouter_configuration(monkeypatch) -> None:
    settings = Settings.model_construct(
        openrouter_api_key="sk-test",
        openrouter_mini_model="qwen/qwen3.5-9b",
        openrouter_frontier_model="anthropic/claude-opus-4-6",
    )
    monkeypatch.setattr(health_mod, "get_settings", lambda: settings)

    payload = health_mod.health_runtime()

    assert payload["openrouter"] == {
        "configured": True,
        "mini_model": "qwen/qwen3.5-9b",
        "frontier_model": "anthropic/claude-opus-4-6",
    }


def test_readiness_returns_healthy_status(monkeypatch) -> None:
    monkeypatch.setattr(health_mod, "get_redis", lambda: _HealthyRedis())

    payload = health_mod.readiness(db=_HealthyDb())

    assert payload == {"ok": True, "status": "healthy", "checks": {"db": "ok", "redis": "ok"}}


def test_readiness_returns_failure_reason_and_logs(monkeypatch, caplog) -> None:
    class BrokenDb:
        def execute(self, _query):
            raise RuntimeError("db down")

    monkeypatch.setattr(health_mod, "get_redis", lambda: _HealthyRedis())

    with caplog.at_level(logging.ERROR):
        response = health_mod.readiness(db=BrokenDb())

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["status"] == "unhealthy"
    assert payload["checks"]["db"] == "error"
    assert payload["reasons"]["db"] == "db down"
    assert "Readiness db check failed" in caplog.text
