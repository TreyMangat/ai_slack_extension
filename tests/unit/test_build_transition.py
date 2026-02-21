from __future__ import annotations

import pytest

from app.models import FeatureRequest
from app.services.feature_service import BuildAlreadyInProgressError, transition_feature_to_building
from app.state_machine import BUILDING, READY_FOR_BUILD


def _feature(status: str, *, job_id: str = "") -> FeatureRequest:
    return FeatureRequest(
        status=status,
        title="Test",
        requester_user_id="user@example.com",
        spec={},
        active_build_job_id=job_id,
    )


def test_transition_to_building_from_ready() -> None:
    feature = _feature(READY_FOR_BUILD)
    transition_feature_to_building(feature)
    assert feature.status == BUILDING


def test_transition_to_building_rejects_duplicate() -> None:
    feature = _feature(BUILDING, job_id="job-123")
    with pytest.raises(BuildAlreadyInProgressError):
        transition_feature_to_building(feature)

