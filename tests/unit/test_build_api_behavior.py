from __future__ import annotations

from app.api.routes.api import _build_idempotent_payload, _build_invalid_detail
from app.models import FeatureRequest


def _feature(*, status: str, active_build_job_id: str = "", missing: list[str] | None = None) -> FeatureRequest:
    validation = {"is_valid": not bool(missing), "missing": missing or [], "warnings": []}
    return FeatureRequest(
        id="feature-123",
        status=status,
        title="Test feature",
        requester_user_id="user-1",
        spec={"_validation": validation},
        active_build_job_id=active_build_job_id,
    )


def test_build_idempotent_payload_uses_existing_job_id() -> None:
    feature = _feature(status="BUILDING", active_build_job_id="job-42")
    payload = _build_idempotent_payload(feature)
    assert payload["ok"] is True
    assert payload["already_in_progress"] is True
    assert payload["job_id"] == "job-42"
    assert payload["status"] == "BUILDING"


def test_build_invalid_detail_includes_missing_fields() -> None:
    feature = _feature(status="NEEDS_INFO", missing=["business_justification", "acceptance_criteria"])
    detail = _build_invalid_detail(feature)
    assert detail["status"] == "NEEDS_INFO"
    assert detail["message"] == "Feature is not ready to build from status NEEDS_INFO"
    assert detail["missing"] == ["business_justification", "acceptance_criteria"]
    assert "next_action" in detail
