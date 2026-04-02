"""Tests for feature_service.py core orchestration behavior."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models import FeatureRequest
from app.schemas import FeatureRequestCreate, FeatureSpecPatch, FeatureSpecUpdateRequest
from app.services.frontier_validator import ValidationResult
from app.services.feature_service import (
    BuildAlreadyInProgressError,
    _apply_llm_validation_artifacts,
    _normalize_string_list,
    _set_validation_metadata,
    _status_after_validation,
    create_feature_request,
    mark_product_approved,
    mark_ready_to_merge,
    refresh_spec_validation,
    transition_feature_to_building,
    update_feature_spec,
)
from app.state_machine import (
    BUILDING,
    FAILED_SPEC,
    MERGED,
    NEEDS_INFO,
    NEW,
    PREVIEW_READY,
    PRODUCT_APPROVED,
    READY_FOR_BUILD,
    READY_TO_MERGE,
)


VALID_SPEC = {
    "title": "Add export button",
    "problem": "Users need to export data",
    "business_justification": "Support teams need offline reporting",
    "acceptance_criteria": ["Button exists", "CSV downloads"],
    "repo": "org/app",
    "implementation_mode": "new_feature",
}


def _feature(*, status: str = NEW, spec: dict | None = None, job_id: str = "") -> FeatureRequest:
    return FeatureRequest(
        status=status,
        title=str((spec or VALID_SPEC).get("title") or "Test feature"),
        requester_user_id="U_TEST",
        spec=dict(spec or VALID_SPEC),
        active_build_job_id=job_id,
    )


def _create_payload(**spec_overrides) -> FeatureRequestCreate:
    spec = dict(VALID_SPEC)
    spec.update(spec_overrides)
    return FeatureRequestCreate(
        spec=spec,
        requester_user_id="U_TEST",
        slack_team_id="T_TEST",
        slack_channel_id="C_TEST",
        slack_thread_ts="123.456",
        slack_message_ts="123.456",
    )


class TestStatusAfterValidation:
    def test_valid_from_new_gives_ready(self):
        assert _status_after_validation(NEW, is_valid=True) == READY_FOR_BUILD

    def test_valid_from_needs_info_gives_ready(self):
        assert _status_after_validation(NEEDS_INFO, is_valid=True) == READY_FOR_BUILD

    def test_valid_from_failed_spec_gives_ready(self):
        assert _status_after_validation(FAILED_SPEC, is_valid=True) == READY_FOR_BUILD

    def test_invalid_from_new_gives_needs_info(self):
        assert _status_after_validation(NEW, is_valid=False) == NEEDS_INFO

    def test_invalid_from_ready_gives_needs_info(self):
        assert _status_after_validation(READY_FOR_BUILD, is_valid=False) == NEEDS_INFO

    def test_invalid_from_failed_spec_gives_needs_info(self):
        assert _status_after_validation(FAILED_SPEC, is_valid=False) == NEEDS_INFO

    def test_valid_from_building_stays(self):
        assert _status_after_validation(BUILDING, is_valid=True) == BUILDING

    def test_invalid_from_building_stays(self):
        assert _status_after_validation(BUILDING, is_valid=False) == BUILDING

    def test_valid_from_preview_ready_stays(self):
        assert _status_after_validation(PREVIEW_READY, is_valid=True) == PREVIEW_READY


class TestTransitionToBuilding:
    def _make_feature(self, status, job_id=""):
        feature = MagicMock()
        feature.status = status
        feature.active_build_job_id = job_id
        return feature

    def test_ready_transitions_to_building(self):
        feature = self._make_feature(READY_FOR_BUILD)
        transition_feature_to_building(feature)
        assert feature.status == BUILDING

    def test_already_building_raises(self):
        feature = self._make_feature(BUILDING, job_id="job-123")
        with pytest.raises(BuildAlreadyInProgressError) as exc_info:
            transition_feature_to_building(feature)
        assert "job-123" in str(exc_info.value)

    def test_wrong_state_raises_value_error(self):
        feature = self._make_feature(NEW)
        with pytest.raises(ValueError, match="READY_FOR_BUILD"):
            transition_feature_to_building(feature)

    def test_needs_info_cannot_build(self):
        feature = self._make_feature(NEEDS_INFO)
        with pytest.raises(ValueError):
            transition_feature_to_building(feature)

    def test_merged_cannot_build(self):
        feature = self._make_feature(MERGED)
        with pytest.raises(ValueError):
            transition_feature_to_building(feature)


class TestBuildAlreadyInProgressError:
    def test_includes_job_id(self):
        err = BuildAlreadyInProgressError(job_id="abc-123")
        assert "abc-123" in str(err)
        assert err.job_id == "abc-123"

    def test_no_job_id(self):
        err = BuildAlreadyInProgressError()
        assert "Build already in progress" in str(err)
        assert err.job_id == ""


class TestValidationHelpers:
    def test_set_validation_metadata_embeds_completion_report(self):
        feature = _feature()
        _set_validation_metadata(feature, is_valid=True, missing=[], warnings=["repo is empty"])

        validation = feature.spec["_validation"]
        assert validation["is_valid"] is True
        assert validation["missing"] == []
        assert validation["warnings"] == ["repo is empty"]
        assert validation["completion"]["score"] > 0

    def test_apply_llm_validation_artifacts_ignores_non_dict_analysis(self, fake_db):
        feature = _feature()
        with patch("app.services.feature_service.log_event") as log_event_mock:
            _apply_llm_validation_artifacts(fake_db, feature, llm_analysis=None)

        assert feature.llm_spec_analysis is None
        log_event_mock.assert_not_called()

    def test_apply_llm_validation_artifacts_skips_empty_cost_and_model(self, fake_db):
        feature = _feature()
        llm_analysis = {"usage": {"input_tokens": 1, "output_tokens": 2}}

        with patch("app.services.feature_service.log_event") as log_event_mock:
            _apply_llm_validation_artifacts(fake_db, feature, llm_analysis=llm_analysis)

        assert feature.llm_spec_analysis == llm_analysis
        log_event_mock.assert_not_called()

    def test_apply_llm_validation_artifacts_logs_cost_event(self, fake_db):
        feature = _feature()
        llm_analysis = {
            "model": "anthropic/claude-opus-4-6",
            "tier": "FRONTIER",
            "usage": {"input_tokens": 12, "output_tokens": 34},
            "cost_estimate_usd": 0.015,
        }

        with patch("app.services.feature_service.log_event") as log_event_mock:
            _apply_llm_validation_artifacts(fake_db, feature, llm_analysis=llm_analysis)

        log_event_mock.assert_called_once()
        assert feature.llm_spec_analysis == llm_analysis
        event_data = log_event_mock.call_args.kwargs["data"]
        assert event_data["tokens_in"] == 12
        assert event_data["tokens_out"] == 34
        assert event_data["cost_usd"] == 0.015

    def test_normalize_string_list_strips_empty_entries(self):
        assert _normalize_string_list([" alpha ", "", "beta", "   "]) == ["alpha", "beta"]


class TestCreateFeatureRequest:
    def test_create_feature_request_sets_metadata_and_ready_status(self, mock_db_session):
        payload = _create_payload(links=["https://example.com/spec", "javascript:alert(1)"])
        frontier_result = ValidationResult(
            is_valid=True,
            confidence=0.94,
            acceptance_criteria=["Button exists", "CSV downloads"],
            reasoning="Specific enough to build",
            model="anthropic/claude-opus-4-6",
            tier="frontier",
            usage={"input_tokens": 20, "output_tokens": 10},
            cost_estimate_usd=0.0025,
        )

        with (
            patch(
                "app.services.feature_service.validate_spec",
                return_value=(True, [], ["repo looks good"]),
            ) as validate_mock,
            patch(
                "app.services.feature_service.validate_spec_with_frontier_sync",
                return_value=frontier_result,
            ) as frontier_mock,
            patch(
                "app.services.feature_service.get_settings",
                return_value=MagicMock(openrouter_api_key="sk-or-test-key"),
            ),
            patch("app.services.feature_service.log_event") as log_event_mock,
        ):
            feature = create_feature_request(mock_db_session, payload)

        assert feature.id
        assert feature.status == READY_FOR_BUILD
        assert feature.requester_user_id == "U_TEST"
        assert feature.spec["links"] == ["https://example.com/spec"]
        assert feature.spec["_meta"]["version"] == 1
        assert feature.spec["_meta"]["last_updated_by"] == "U_TEST"
        assert feature.spec["_validation"]["is_valid"] is True
        assert feature.spec["_frontier_validation"]["is_valid"] is True
        assert feature.spec["acceptance_criteria"] == ["Button exists", "CSV downloads"]
        assert feature.llm_spec_analysis is not None
        assert feature.llm_spec_analysis["model"] == "anthropic/claude-opus-4-6"
        assert feature.slack_channel_id == "C_TEST"
        assert "optimized_prompt" in feature.spec
        validate_mock.assert_called_once()
        frontier_mock.assert_called_once()
        called_spec = validate_mock.call_args.args[0]
        assert called_spec["links"] == ["https://example.com/spec"]
        assert "_validation" not in called_spec
        assert log_event_mock.call_count == 3

    def test_create_feature_request_invalid_spec_moves_to_needs_info(self, mock_db_session):
        payload = _create_payload(problem="   ")

        with (
            patch(
                "app.services.feature_service.validate_spec",
                return_value=(False, ["problem"], ["problem is vague"]),
            ) as validate_mock,
            patch("app.services.feature_service.validate_spec_with_frontier_sync") as frontier_mock,
            patch("app.services.feature_service.log_event") as log_event_mock,
        ):
            feature = create_feature_request(mock_db_session, payload)

        assert feature.status == NEEDS_INFO
        assert feature.llm_spec_analysis is None
        assert feature.spec["_validation"]["missing"] == ["problem"]
        assert feature.spec["_validation"]["warnings"] == ["problem is vague"]
        validate_mock.assert_called_once()
        frontier_mock.assert_not_called()
        assert log_event_mock.call_count == 2


class TestRefreshSpecValidation:
    def test_refresh_spec_validation_keeps_building_state_when_invalid(self, fake_db):
        feature = _feature(status=BUILDING)

        with (
            patch(
                "app.services.feature_service.validate_spec_with_llm_sync",
                return_value=(False, ["problem"], ["missing detail"], None),
            ),
            patch("app.services.feature_service.log_event") as log_event_mock,
        ):
            updated = refresh_spec_validation(fake_db, feature)

        assert updated is feature
        assert feature.status == BUILDING
        assert feature.spec["_validation"]["is_valid"] is False
        log_event_mock.assert_called_once()

    def test_refresh_spec_validation_promotes_failed_spec_when_valid(self, fake_db):
        feature = _feature(status=FAILED_SPEC)

        with (
            patch(
                "app.services.feature_service.validate_spec_with_llm_sync",
                return_value=(True, [], [], None),
            ),
            patch("app.services.feature_service.log_event") as log_event_mock,
        ):
            refresh_spec_validation(fake_db, feature)

        assert feature.status == READY_FOR_BUILD
        log_event_mock.assert_called_once()


class TestUpdateFeatureSpec:
    def test_update_feature_spec_normalizes_fields_and_bumps_meta(self, fake_db):
        feature = _feature(
            status=NEEDS_INFO,
            spec={
                **VALID_SPEC,
                "_meta": {"version": 4, "last_updated_by": "U_OLD"},
                "links": [],
            },
        )
        payload = FeatureSpecUpdateRequest(
            spec=FeatureSpecPatch(
                title="  Updated title  ",
                acceptance_criteria=[" first ", "", "second "],
                links=["javascript:alert(1)", "https://example.com/doc"],
                source_repos=[" repo-one ", " ", "repo-two "],
            ),
            actor_type="slack",
            actor_id="U_EDITOR",
            message="Spec updated from Slack",
        )

        with (
            patch("app.services.feature_service.refresh_spec_validation", side_effect=lambda db, value: value) as refresh_mock,
            patch("app.services.feature_service.log_event") as log_event_mock,
        ):
            updated = update_feature_spec(fake_db, feature, payload)

        assert updated is feature
        assert feature.title == "Updated title"
        assert feature.spec["acceptance_criteria"] == ["first", "second"]
        assert feature.spec["source_repos"] == ["repo-one", "repo-two"]
        assert feature.spec["links"] == ["https://example.com/doc"]
        assert feature.spec["_meta"]["version"] == 5
        assert feature.spec["_meta"]["last_updated_by"] == "U_EDITOR"
        assert feature.spec["_meta"]["last_updated_fields"] == [
            "acceptance_criteria",
            "links",
            "source_repos",
            "title",
        ]
        assert "optimized_prompt" in feature.spec
        refresh_mock.assert_called_once_with(fake_db, feature)
        log_event_mock.assert_called_once()

    def test_update_feature_spec_rejects_terminal_state(self, fake_db):
        feature = _feature(status=MERGED)
        payload = FeatureSpecUpdateRequest(spec=FeatureSpecPatch(title="Updated"))

        with pytest.raises(ValueError, match="terminal state"):
            update_feature_spec(fake_db, feature, payload)

    def test_update_feature_spec_rejects_empty_patch(self, fake_db):
        feature = _feature()
        payload = FeatureSpecUpdateRequest(spec=FeatureSpecPatch())

        with pytest.raises(ValueError, match="No spec fields provided"):
            update_feature_spec(fake_db, feature, payload)


class TestApprovalAndMergeTransitions:
    def test_mark_product_approved_sets_fields_and_logs_event(self, fake_db):
        feature = _feature(status=PREVIEW_READY)

        with (
            patch("app.services.feature_service.ensure_approver_allowed") as approver_mock,
            patch("app.services.feature_service.log_event") as log_event_mock,
        ):
            updated = mark_product_approved(fake_db, feature, approver="U_APPROVER")

        assert updated is feature
        assert feature.status == PRODUCT_APPROVED
        assert feature.product_approved_by == "U_APPROVER"
        assert feature.product_approved_at is not None
        approver_mock.assert_called_once_with("U_APPROVER")
        log_event_mock.assert_called_once()

    def test_mark_product_approved_skips_auth_check_when_preauthorized(self, fake_db):
        feature = _feature(status=PREVIEW_READY)

        with (
            patch("app.services.feature_service.ensure_approver_allowed") as approver_mock,
            patch("app.services.feature_service.log_event"),
        ):
            mark_product_approved(fake_db, feature, approver="U_APPROVER", preauthorized=True)

        approver_mock.assert_not_called()
        assert feature.status == PRODUCT_APPROVED

    def test_mark_ready_to_merge_updates_status_and_logs_event(self, fake_db):
        feature = _feature(status=PRODUCT_APPROVED)

        with patch("app.services.feature_service.log_event") as log_event_mock:
            updated = mark_ready_to_merge(fake_db, feature, actor_id="system-bot")

        assert updated is feature
        assert feature.status == READY_TO_MERGE
        log_event_mock.assert_called_once()
