from __future__ import annotations

import pytest
from starlette.requests import Request

from app.config import get_settings
from app.security import (
    require_authenticated_user,
    user_can_access_feature,
    user_can_view_all_features,
)


def _request(headers: dict[str, str] | None = None) -> Request:
    encoded_headers = []
    for key, value in (headers or {}).items():
        encoded_headers.append((key.lower().encode("utf-8"), value.encode("utf-8")))
    scope = {"type": "http", "method": "GET", "path": "/", "headers": encoded_headers}
    return Request(scope)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_disabled_mode_returns_local_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "disabled")
    user = require_authenticated_user(_request())
    assert user.auth_source == "disabled"
    assert user.actor_id == "local-user"


def test_edge_sso_parses_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "edge_sso")
    user = require_authenticated_user(
        _request(
            {
                "X-Forwarded-Email": "user@example.com",
                "X-Forwarded-Groups": "engineering,admins",
            }
        )
    )
    assert user.email == "user@example.com"
    assert "engineering" in user.groups
    assert user_can_view_all_features(user)


def test_feature_access_scoped_to_requester(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "edge_sso")
    monkeypatch.setenv("RBAC_BUILDERS", "group:engineering")
    user = require_authenticated_user(
        _request(
            {
                "X-Forwarded-Email": "requester@example.com",
                "X-Forwarded-Groups": "product",
            }
        )
    )
    assert user_can_access_feature(user, "requester@example.com")
    assert not user_can_access_feature(user, "someone-else@example.com")

