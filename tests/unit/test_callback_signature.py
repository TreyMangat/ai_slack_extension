from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes.api import _verify_execution_callback_signature
from app.config import get_settings


def _request(headers: dict[str, str]) -> Request:
    encoded_headers = [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()]
    scope = {"type": "http", "method": "POST", "path": "/api/integrations/execution-callback", "headers": encoded_headers}
    return Request(scope)


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTEGRATION_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("INTEGRATION_WEBHOOK_TTL_SECONDS", "300")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_valid_callback_signature_passes() -> None:
    body = b'{"feature_id":"abc","event":"preview_ready","event_id":"evt-1"}'
    ts = str(int(time.time()))
    digest = hmac.new(b"test-secret", f"{ts}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    request = _request(
        {
            "X-Feature-Factory-Timestamp": ts,
            "X-Feature-Factory-Signature": f"sha256={digest}",
        }
    )
    _verify_execution_callback_signature(request, body)


def test_invalid_callback_signature_fails() -> None:
    body = b'{"feature_id":"abc","event":"preview_ready","event_id":"evt-1"}'
    ts = str(int(time.time()))
    request = _request(
        {
            "X-Feature-Factory-Timestamp": ts,
            "X-Feature-Factory-Signature": "sha256=invalid",
        }
    )
    with pytest.raises(HTTPException):
        _verify_execution_callback_signature(request, body)

