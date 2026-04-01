"""Shared test fixtures for the PRFactory test suite.

Provides environment-backed settings, OpenRouter response factories,
lightweight fake objects, and an in-memory SQLAlchemy session with all
tables created (SQLite-compatible).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models import Base


# ---------------------------------------------------------------------------
# SQLite <-> JSONB compatibility
# ---------------------------------------------------------------------------
# The production models use PostgreSQL JSONB.  For in-memory SQLite tests we
# register a type compiler so JSONB / PG-JSON render as plain JSON on SQLite.
from sqlalchemy.dialects.postgresql import JSON as PG_JSON, JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _compile_jsonb(element, compiler, **kw):
    return "JSON"


@compiles(PG_JSON, "sqlite")
def _compile_pg_json(element, compiler, **kw):
    return "JSON"


# ---------------------------------------------------------------------------
# Settings cache management
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Ensure each test gets fresh settings (lru_cache)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings(monkeypatch):
    """Settings with OpenRouter configured, mock mode, auth disabled."""
    env = {
        "APP_ENV": "test",
        "BASE_URL": "http://localhost:8000",
        "ORCHESTRATOR_INTERNAL_URL": "http://api:8000",
        "DATABASE_URL": "sqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379",
        "SECRET_KEY": "test-secret",
        "MOCK_MODE": "true",
        "AUTH_MODE": "disabled",
        "ENABLE_SLACK_BOT": "false",
        "SLACK_MODE": "socket",
        "SLACK_BOT_TOKEN": "",
        "SLACK_APP_TOKEN": "",
        "SLACK_SIGNING_SECRET": "",
        "GITHUB_ENABLED": "false",
        "API_AUTH_TOKEN": "test-token",
        "DISABLE_AUTOMERGE": "true",
        "CODERUNNER_MODE": "opencode",
        "OPENROUTER_API_KEY": "sk-or-test-key",
        "OPENROUTER_MINI_MODEL": "qwen/qwen3.5-9b",
        "OPENROUTER_FRONTIER_MODEL": "anthropic/claude-opus-4-6",
        "OPENROUTER_BUDGET_LIMIT_USD": "5.0",
        "OPENROUTER_REFERER": "https://example.com",
        "OPENROUTER_APP_TITLE": "TestApp",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return get_settings()


@pytest.fixture
def mock_openrouter_response():
    """Factory for fake OpenRouter HTTP responses (raw dict)."""
    def _make(content, model="qwen/qwen3.5-9b", input_tokens=50, output_tokens=100):
        return {
            "choices": [{"message": {"content": content}}],
            "model": model,
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
        }
    return _make


@pytest.fixture
def mock_db_session():
    """In-memory SQLAlchemy session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    _Session = sessionmaker(bind=engine)
    session = _Session()
    yield session
    session.close()


@pytest.fixture
def fake_feature():
    """Return a minimal FeatureRequest-like dict."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "status": "NEW",
        "title": "Test feature",
        "created_at": now,
        "updated_at": now,
        "requester_user_id": "U_TEST",
        "spec": {
            "title": "Test feature",
            "problem": "Test problem description",
            "business_justification": "Test business value",
            "acceptance_criteria": ["It works"],
        },
        "slack_channel_id": "C_TEST",
        "slack_thread_ts": "1234567890.123456",
        "github_issue_url": "",
        "github_pr_url": "",
        "preview_url": "",
    }


@pytest.fixture
def fake_db():
    """Return a mock DB session."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.commit = MagicMock()
    db.rollback = MagicMock()
    return db
