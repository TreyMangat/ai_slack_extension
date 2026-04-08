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
    )


def test_pr_opened_callback_moves_building_to_pr_opened() -> None:
    feature = _feature(BUILDING)
    payload = ExecutionCallbackIn(
        feature_id="feature-1",
        event="pr_opened",
        github_pr_url="https://github.com/example/repo/pull/29",
    )

    _apply_execution_callback(feature, payload)

    assert feature.status == PR_OPENED
    assert feature.github_pr_url == "https://github.com/example/repo/pull/29"


def test_preview_ready_callback_promotes_pr_opened_to_preview_ready() -> None:
    feature = _feature(PR_OPENED)
    feature.github_pr_url = "https://github.com/example/repo/pull/29"
    feature.active_build_job_id = "job-29"

    payload = ExecutionCallbackIn(
        feature_id="feature-1",
        event="preview_ready",
        github_pr_url=feature.github_pr_url,
        preview_url="https://preview.example.dev/feature-29",
    )

    _apply_execution_callback(feature, payload)

    assert feature.status == PREVIEW_READY
    assert feature.preview_url == "https://preview.example.dev/feature-29"
    assert feature.active_build_job_id == ""
