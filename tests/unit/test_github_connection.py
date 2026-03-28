"""Tests for GitHub OAuth connection checker."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import get_settings
from app.services.github_connection import (
    GitHubConnectionCheck,
    GitHubConnectionStatus,
    check_github_connection,
    refresh_github_token,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    defaults = {
        "DATABASE_URL": "sqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379",
        "SECRET_KEY": "test-secret",
        "GITHUB_API_BASE": "https://api.github.com",
        "GITHUB_OAUTH_CLIENT_ID": "test-client-id",
        "GITHUB_OAUTH_CLIENT_SECRET": "test-client-secret",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


def _mock_connection_dict(*, github_login="testuser", token="gho_valid123"):
    """Return a dict like _lookup_connection would return."""
    return {
        "github_login": github_login,
        "github_user_id": "12345",
        "access_token_encrypted": token,  # will be "decrypted" by mock
        "created_at": "2026-01-15 10:00:00",
    }


def _mock_httpx_response(status_code, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# check_github_connection
# ---------------------------------------------------------------------------


class TestConnectedUser:
    @pytest.mark.asyncio
    async def test_connected_user_returns_connected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = _mock_httpx_response(200, {"login": "testuser", "id": 12345})

        with patch(
            "app.services.github_connection._lookup_connection",
            return_value=_mock_connection_dict(),
        ), patch(
            "app.services.github_user_oauth._decrypt_user_token",
            return_value="gho_valid123",
        ), patch(
            "app.services.github_connection.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await check_github_connection("U123")

        assert result.status == GitHubConnectionStatus.CONNECTED
        assert result.repos_available is True
        assert result.username == "testuser"


class TestExpiredToken:
    @pytest.mark.asyncio
    async def test_expired_token_returns_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = _mock_httpx_response(401)

        with patch(
            "app.services.github_connection._lookup_connection",
            return_value=_mock_connection_dict(),
        ), patch(
            "app.services.github_user_oauth._decrypt_user_token",
            return_value="gho_expired",
        ), patch(
            "app.services.github_connection.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await check_github_connection("U123")

        assert result.status == GitHubConnectionStatus.EXPIRED
        assert result.repos_available is False
        assert result.username == "testuser"


class TestNoConnection:
    @pytest.mark.asyncio
    async def test_no_connection_returns_not_connected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        with patch(
            "app.services.github_connection._lookup_connection",
            return_value=None,
        ):
            result = await check_github_connection("U999")

        assert result.status == GitHubConnectionStatus.NOT_CONNECTED
        assert result.repos_available is False


class TestRateLimited:
    @pytest.mark.asyncio
    async def test_rate_limited_returns_rate_limited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = _mock_httpx_response(
            403,
            {"message": "rate limit exceeded"},
            headers={"X-RateLimit-Remaining": "0"},
        )

        with patch(
            "app.services.github_connection._lookup_connection",
            return_value=_mock_connection_dict(),
        ), patch(
            "app.services.github_user_oauth._decrypt_user_token",
            return_value="gho_valid",
        ), patch(
            "app.services.github_connection.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await check_github_connection("U123")

        assert result.status == GitHubConnectionStatus.RATE_LIMITED
        assert result.repos_available is False


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_not_connected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = httpx.ReadTimeout("timed out")

        with patch(
            "app.services.github_connection._lookup_connection",
            return_value=_mock_connection_dict(),
        ), patch(
            "app.services.github_user_oauth._decrypt_user_token",
            return_value="gho_valid",
        ), patch(
            "app.services.github_connection.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await check_github_connection("U123")

        assert result.status == GitHubConnectionStatus.NOT_CONNECTED
        assert result.repos_available is False
        assert "unreachable" in result.message.lower() or "timed out" in result.message.lower()


class TestEmptySlackUserId:
    @pytest.mark.asyncio
    async def test_empty_user_id_returns_not_connected(self) -> None:
        result = await check_github_connection("")
        assert result.status == GitHubConnectionStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# refresh_github_token
# ---------------------------------------------------------------------------


class TestRefreshTokenSuccess:
    @pytest.mark.asyncio
    async def test_refresh_token_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        # Create a mock connection row with refresh_token_encrypted
        mock_row = MagicMock()
        mock_row.refresh_token_encrypted = "encrypted_refresh"
        mock_row.access_token_encrypted = "old_encrypted"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = _mock_httpx_response(200, {
            "access_token": "gho_new_token",
            "refresh_token": "ghr_new_refresh",
        })

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.return_value.scalars.return_value.first.return_value = mock_row

        with patch(
            "app.services.github_connection._lookup_connection_row",
            return_value=mock_row,
        ), patch(
            "app.services.github_user_oauth._decrypt_user_token",
            return_value="refresh_token_value",
        ), patch(
            "app.services.github_user_oauth._encrypt_user_token",
            side_effect=lambda t: f"encrypted_{t}",
        ), patch(
            "app.services.github_connection.httpx.AsyncClient",
            return_value=mock_client,
        ), patch(
            "app.db.db_session",
            return_value=mock_session,
        ):
            result = await refresh_github_token("U123")

        assert result is True


class TestRefreshTokenFailure:
    @pytest.mark.asyncio
    async def test_refresh_token_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        # No refresh token available
        mock_row = MagicMock()
        mock_row.refresh_token_encrypted = ""

        with patch(
            "app.services.github_connection._lookup_connection_row",
            return_value=mock_row,
        ), patch(
            "app.services.github_user_oauth._decrypt_user_token",
            return_value="",
        ):
            result = await refresh_github_token("U123")

        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_no_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_settings(monkeypatch)

        with patch(
            "app.services.github_connection._lookup_connection_row",
            return_value=None,
        ):
            result = await refresh_github_token("U123")

        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_empty_user_id(self) -> None:
        result = await refresh_github_token("")
        assert result is False
