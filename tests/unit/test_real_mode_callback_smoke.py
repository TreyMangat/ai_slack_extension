from __future__ import annotations

from app.api.routes.api import _apply_execution_callback
from app.models import FeatureRequest
from app.schemas import ExecutionCallbackIn
from app.state_machine import BUILDING, PREVIEW_READY, PR_OPENED


def _feature(status: str) -> FeatureRequest:
    return FeatureRequest(
        status=status,
        title="Real mode callback smoke",
        requester_user_id="smoke@example.local",
        spec={},
        active_build_job_id="job-123",
    )


def test_real_mode_flow_build_to_pr_opened_then_preview_ready() -> None:
    feature = _feature(BUILDING)

    _apply_execution_callback(
        feature,
        ExecutionCallbackIn(
            feature_id="feature-1",
            event="pr_opened",
            github_pr_url="https://github.com/example/repo/pull/39",
        ),
    )
    assert feature.status == PR_OPENED
    assert feature.github_pr_url == "https://github.com/example/repo/pull/39"

    _apply_execution_callback(
        feature,
        ExecutionCallbackIn(
            feature_id="feature-1",
            event="preview_ready",
            preview_url="https://preview.example.com/feature-39",
        ),
    )
    assert feature.status == PREVIEW_READY
    assert feature.preview_url == "https://preview.example.com/feature-39"
    assert feature.active_build_job_id == ""


def test_real_mode_preview_ready_callback_can_promote_from_building() -> None:
    feature = _feature(BUILDING)

    _apply_execution_callback(
        feature,
        ExecutionCallbackIn(
            feature_id="feature-1",
            event="preview_ready",
            preview_url="https://preview.example.com/feature-39",
        ),
    )

    assert feature.status == PREVIEW_READY
    assert feature.preview_url == "https://preview.example.com/feature-39"
