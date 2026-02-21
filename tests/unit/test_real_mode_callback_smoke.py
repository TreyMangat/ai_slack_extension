from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from starlette.requests import Request

from app.api.routes.api import _apply_execution_callback, _verify_execution_callback_signature
from app.config import get_settings
from app.models import FeatureRequest
from app.schemas import ExecutionCallbackIn
from app.state_machine import BUILDING, PREVIEW_READY, PR_OPENED


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


def _feature(status: str = BUILDING) -> FeatureRequest:
    return FeatureRequest(
        status=status,
        title="Real mode callback smoke",
        requester_user_id="smoke-test",
        spec={},
        active_build_job_id="job-123",
    )


def test_real_mode_signed_callback_smoke_flow() -> None:
    feature = _feature(BUILDING)

    pr_payload = ExecutionCallbackIn(
        feature_id="feature-1",
        event="pr_opened",
        github_pr_url="https://example.com/org/repo/pull/68",
        message="PR opened by external runner",
        event_id="evt-pr-68",
    )
    _apply_execution_callback(feature, pr_payload)

    assert feature.status == PR_OPENED
    assert feature.github_pr_url == "https://example.com/org/repo/pull/68"

    callback_body = {
        "feature_id": "feature-1",
        "event": "preview_ready",
        "github_pr_url": "https://example.com/org/repo/pull/68",
        "preview_url": "https://preview.example.com/ff-68",
        "message": "Preview deployed",
        "event_id": "evt-preview-68",
    }
    raw_body = json.dumps(callback_body, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time()))
    digest = hmac.new(b"test-secret", f"{ts}.".encode("utf-8") + raw_body, hashlib.sha256).hexdigest()

    request = _request(
        {
            "X-Feature-Factory-Timestamp": ts,
            "X-Feature-Factory-Signature": f"sha256={digest}",
            "X-Feature-Factory-Event-Id": "evt-preview-68",
        }
    )

    _verify_execution_callback_signature(request, raw_body)
    _apply_execution_callback(feature, ExecutionCallbackIn.model_validate(callback_body))

    assert feature.status == PREVIEW_READY
    assert feature.preview_url == "https://preview.example.com/ff-68"
    assert feature.active_build_job_id == ""
