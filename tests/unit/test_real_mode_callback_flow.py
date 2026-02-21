from __future__ import annotations

from app.api.routes.api import _apply_execution_callback
from app.models import FeatureRequest
from app.schemas import ExecutionCallbackIn
from app.state_machine import BUILDING, PREVIEW_READY, PR_OPENED


def _feature(status: str) -> FeatureRequest:
    return FeatureRequest(
        id="feature-real-mode",
        status=status,
        title="Real mode callback smoke",
        requester_user_id="smoke-user",
        spec={},
        active_build_job_id="job-123",
    )


def test_preview_ready_callback_promotes_pr_opened_to_preview_ready() -> None:
    feature = _feature(PR_OPENED)
    payload = ExecutionCallbackIn(
        feature_id=feature.id,
        event="preview_ready",
        preview_url="https://preview.example.local/feature-real-mode",
        github_pr_url="https://github.com/acme/repo/pull/49",
    )

    _apply_execution_callback(feature, payload)

    assert feature.status == PREVIEW_READY
    assert feature.preview_url == "https://preview.example.local/feature-real-mode"
    assert feature.github_pr_url == "https://github.com/acme/repo/pull/49"
    assert feature.active_build_job_id == ""


def test_preview_ready_callback_allows_direct_transition_from_building() -> None:
    feature = _feature(BUILDING)
    payload = ExecutionCallbackIn(
        feature_id=feature.id,
        event="preview_ready",
        preview_url="https://preview.example.local/direct",
    )

    _apply_execution_callback(feature, payload)

    assert feature.status == PREVIEW_READY
    assert feature.preview_url == "https://preview.example.local/direct"
