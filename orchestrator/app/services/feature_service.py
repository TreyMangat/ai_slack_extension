from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import FeatureRequest
from app.schemas import FeatureRequestCreate, FeatureSpecUpdateRequest
from app.services.event_logger import log_event
from app.services.prompt_optimizer import attach_optimized_prompt
from app.services.reviewer_service import ensure_approver_allowed
from app.services.spec_validator import spec_completion_report, validate_spec_with_llm_sync
from app.services.url_safety import normalize_external_url_list
from app.state_machine import (
    BUILDING,
    FAILED_SPEC,
    NEEDS_INFO,
    NEW,
    READY_FOR_BUILD,
    TERMINAL_STATES,
    perform_action,
    validate_transition,
)


class BuildAlreadyInProgressError(ValueError):
    def __init__(self, *, job_id: str = "") -> None:
        self.job_id = job_id
        message = "Build already in progress"
        if job_id:
            message = f"{message} (job_id={job_id})"
        super().__init__(message)


def _set_validation_metadata(feature: FeatureRequest, *, is_valid: bool, missing: list[str], warnings: list[str]) -> None:
    spec = dict(feature.spec or {})
    completion = spec_completion_report(spec)
    spec["_validation"] = {
        "is_valid": is_valid,
        "missing": missing,
        "warnings": warnings,
        "completion": completion,
    }
    feature.spec = spec


def _status_after_validation(current_status: str, *, is_valid: bool) -> str:
    if is_valid:
        if current_status in {NEW, NEEDS_INFO, FAILED_SPEC}:
            return READY_FOR_BUILD
        return current_status

    if current_status in {NEW, READY_FOR_BUILD, FAILED_SPEC}:
        return NEEDS_INFO
    return current_status


def _apply_llm_validation_artifacts(
    db: Session,
    feature: FeatureRequest,
    *,
    llm_analysis: dict[str, object] | None,
) -> None:
    feature.llm_spec_analysis = llm_analysis if isinstance(llm_analysis, dict) else None
    if not isinstance(llm_analysis, dict):
        return

    usage = llm_analysis.get("usage") if isinstance(llm_analysis.get("usage"), dict) else {}
    cost_usd = float(llm_analysis.get("cost_estimate_usd") or 0.0)
    model = str(llm_analysis.get("model") or "").strip()
    tier = str(llm_analysis.get("tier") or "").strip().lower()

    if not model and cost_usd <= 0.0:
        return

    log_event(
        db,
        feature,
        event_type="llm_cost",
        actor_type="system",
        message=f"LLM call (spec_validation): {model or 'unknown model'} [{tier or 'unknown'}] ${cost_usd:.6f}",
        data={
            "tier": tier,
            "model": model,
            "tokens_in": int(usage.get("input_tokens") or 0),
            "tokens_out": int(usage.get("output_tokens") or 0),
            "cost_usd": round(cost_usd, 6),
            "operation": "spec_validation",
        },
    )


def create_feature_request(db: Session, payload: FeatureRequestCreate) -> FeatureRequest:
    spec_data = payload.spec.model_dump()
    spec_data["links"] = normalize_external_url_list(spec_data.get("links") or [])
    spec_data = attach_optimized_prompt(spec_data)
    spec_meta = dict(spec_data.get("_meta") or {})
    spec_meta["version"] = int(spec_meta.get("version") or 1)
    spec_meta["last_updated_by"] = payload.requester_user_id or "system"
    spec_data["_meta"] = spec_meta

    feature = FeatureRequest(
        status=NEW,
        title=payload.spec.title,
        requester_user_id=payload.requester_user_id or "",
        slack_team_id=payload.slack_team_id or "",
        slack_channel_id=payload.slack_channel_id or "",
        slack_thread_ts=payload.slack_thread_ts or "",
        slack_message_ts=payload.slack_message_ts or "",
        spec=spec_data,
    )
    db.add(feature)
    db.flush()  # assign id

    log_event(
        db,
        feature,
        event_type="created",
        actor_type="user" if payload.requester_user_id else "system",
        actor_id=payload.requester_user_id or "",
        message="Feature request created",
        data={"spec": feature.spec},
    )

    # Validate spec immediately and update state
    is_valid, missing, warnings, llm_analysis = validate_spec_with_llm_sync(feature.spec, feature_id=feature.id)
    _set_validation_metadata(feature, is_valid=is_valid, missing=missing, warnings=warnings)
    _apply_llm_validation_artifacts(db, feature, llm_analysis=llm_analysis)

    new_status = _status_after_validation(feature.status, is_valid=is_valid)
    if new_status != feature.status:
        validate_transition(feature.status, new_status)
        feature.status = new_status

    log_event(
        db,
        feature,
        event_type="spec_validated",
        actor_type="system",
        message=(
            "Spec valid; ready to build" if is_valid else f"Spec incomplete; missing: {', '.join(missing)}"
        ),
        data={"missing": missing, "warnings": warnings},
    )

    return feature


def refresh_spec_validation(db: Session, feature: FeatureRequest) -> FeatureRequest:
    is_valid, missing, warnings, llm_analysis = validate_spec_with_llm_sync(feature.spec, feature_id=feature.id)
    _set_validation_metadata(feature, is_valid=is_valid, missing=missing, warnings=warnings)
    _apply_llm_validation_artifacts(db, feature, llm_analysis=llm_analysis)

    # Move state based on validation only when a meaningful transition is allowed.
    new_status = _status_after_validation(feature.status, is_valid=is_valid)
    if new_status != feature.status:
        validate_transition(feature.status, new_status)
        feature.status = new_status

    log_event(
        db,
        feature,
        event_type="spec_revalidated",
        actor_type="system",
        message=(
            "Spec valid; ready to build" if is_valid else f"Spec incomplete; missing: {', '.join(missing)}"
        ),
        data={"missing": missing, "warnings": warnings},
    )
    return feature


def _normalize_string_list(values: list[str]) -> list[str]:
    return [str(v).strip() for v in values if str(v).strip()]


def update_feature_spec(db: Session, feature: FeatureRequest, payload: FeatureSpecUpdateRequest) -> FeatureRequest:
    if feature.status in TERMINAL_STATES:
        raise ValueError(f"Cannot update spec in terminal state {feature.status}")

    raw_patch = payload.spec.model_dump(exclude_unset=True)
    if len(raw_patch) == 0:
        raise ValueError("No spec fields provided in update payload")

    normalized_patch: dict[str, object] = {}
    for key, value in raw_patch.items():
        if isinstance(value, str):
            normalized_patch[key] = value.strip()
        elif isinstance(value, list):
            normalized_patch[key] = _normalize_string_list(value)
        else:
            normalized_patch[key] = value

    next_spec = dict(feature.spec or {})
    next_spec.update(normalized_patch)
    next_spec["links"] = normalize_external_url_list(next_spec.get("links") or [])
    next_spec = attach_optimized_prompt(next_spec)
    meta = dict(next_spec.get("_meta") or {})
    prior_version = int(meta.get("version") or 1)
    meta["version"] = prior_version + 1
    meta["last_updated_by"] = payload.actor_id or "system"
    meta["last_updated_fields"] = sorted(normalized_patch.keys())
    next_spec["_meta"] = meta
    feature.spec = next_spec
    feature.title = str(next_spec.get("title", "")).strip()[:200]

    log_event(
        db,
        feature,
        event_type="spec_updated",
        actor_type=payload.actor_type,
        actor_id=payload.actor_id,
        message=payload.message,
        data={"changed_fields": sorted(normalized_patch.keys())},
    )

    return refresh_spec_validation(db, feature)


def mark_product_approved(
    db: Session,
    feature: FeatureRequest,
    *,
    approver: str,
    preauthorized: bool = False,
) -> FeatureRequest:
    if not preauthorized:
        ensure_approver_allowed(approver)

    action_result = perform_action(feature.status, "approve")
    validate_transition(feature.status, action_result.new_status)

    feature.status = action_result.new_status
    feature.product_approved_by = approver
    feature.product_approved_at = datetime.utcnow()

    log_event(
        db,
        feature,
        event_type="product_approved",
        actor_type="user",
        actor_id=approver,
        message="Product approved",
    )
    return feature


def mark_ready_to_merge(db: Session, feature: FeatureRequest, *, actor_id: str = "system") -> FeatureRequest:
    action_result = perform_action(feature.status, "ready_to_merge")
    validate_transition(feature.status, action_result.new_status)
    feature.status = action_result.new_status

    log_event(
        db,
        feature,
        event_type="ready_to_merge",
        actor_type="system",
        actor_id=actor_id,
        message="Marked READY_TO_MERGE",
    )
    return feature


def transition_feature_to_building(feature: FeatureRequest) -> None:
    if feature.status == BUILDING:
        raise BuildAlreadyInProgressError(job_id=(feature.active_build_job_id or "").strip())
    if feature.status != READY_FOR_BUILD:
        raise ValueError(f"Feature must be READY_FOR_BUILD (currently {feature.status})")

    action_result = perform_action(feature.status, "start_build")
    validate_transition(feature.status, action_result.new_status)
    feature.status = action_result.new_status
