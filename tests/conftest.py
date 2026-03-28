"""Shared test fixtures for the PRFactory test suite.

Provides mock settings, OpenRouter response factories, and an in-memory
SQLAlchemy session with all tables created (SQLite-compatible).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
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
        "DATABASE_URL": "sqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379",
        "SECRET_KEY": "test-secret",
        "MOCK_MODE": "true",
        "AUTH_MODE": "disabled",
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
