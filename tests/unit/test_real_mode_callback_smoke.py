from __future__ import annotations

from app.api.routes.api import _apply_execution_callback
from app.models import FeatureRequest
from app.schemas import ExecutionCallbackIn
from app.state_machine import BUILDING, PREVIEW_READY, PR_OPENED


def _feature(*, status: str, active_build_job_id: str = "job-1") -> FeatureRequest:
    return FeatureRequest(
        status=status,
        title="Real mode callback smoke",
        requester_user_id="real-mode-smoke",
        spec={},
        active_build_job_id=active_build_job_id,
    )


def test_real_mode_flow_build_to_pr_then_callback_to_preview_ready() -> None:
    feature = _feature(status=BUILDING)

    _apply_execution_callback(
        feature,
        ExecutionCallbackIn(
            feature_id="feature-1",
            event="pr_opened",
            github_pr_url="https://github.com/acme/repo/pull/60",
        ),
    )

    assert feature.status == PR_OPENED
    assert feature.github_pr_url == "https://github.com/acme/repo/pull/60"
    assert feature.active_build_job_id == "job-1"

    _apply_execution_callback(
        feature,
        ExecutionCallbackIn(
            feature_id="feature-1",
            event="preview_ready",
            preview_url="https://preview.example.com/feature-60",
        ),
    )

    assert feature.status == PREVIEW_READY
    assert feature.preview_url == "https://preview.example.com/feature-60"
    assert feature.active_build_job_id == ""
