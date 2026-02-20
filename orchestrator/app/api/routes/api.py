from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import FeatureRequest
from app.queue import get_queue
from app.security import require_api_auth
from app.schemas import (
    BuildRequest,
    ExecutionCallbackIn,
    FeatureRequestCreate,
    FeatureRequestOut,
    FeatureSpecUpdateRequest,
)
from app.services.event_logger import log_event
from app.services.feature_service import (
    create_feature_request,
    mark_product_approved,
    refresh_spec_validation,
    update_feature_spec,
)
from app.services.reviewer_service import notify_reviewer_for_approval
from app.services.slack_adapter import get_slack_adapter
from app.state_machine import (
    BUILDING,
    FAILED_BUILD,
    FAILED_PREVIEW,
    PREVIEW_READY,
    PR_OPENED,
    READY_FOR_BUILD,
    perform_action,
    validate_transition,
)
from app.tasks.jobs import kickoff_build_job

router = APIRouter()


def _feature_to_out(feature: FeatureRequest) -> FeatureRequestOut:
    return FeatureRequestOut(
        id=feature.id,
        created_at=feature.created_at,
        updated_at=feature.updated_at,
        status=feature.status,
        title=feature.title,
        requester_user_id=feature.requester_user_id,
        slack_channel_id=feature.slack_channel_id,
        slack_thread_ts=feature.slack_thread_ts,
        spec=feature.spec,
        github_issue_url=feature.github_issue_url,
        github_pr_url=feature.github_pr_url,
        preview_url=feature.preview_url,
        product_approved_by=feature.product_approved_by,
        product_approved_at=feature.product_approved_at,
        last_error=feature.last_error,
        events=[
            {
                "id": e.id,
                "created_at": e.created_at,
                "actor_type": e.actor_type,
                "actor_id": e.actor_id,
                "event_type": e.event_type,
                "message": e.message,
                "data": e.data,
            }
            for e in sorted(feature.events, key=lambda x: x.created_at)
        ],
    )


def _verify_execution_callback_signature(request: Request, raw_body: bytes) -> None:
    settings = get_settings()
    secret = settings.integration_webhook_secret.strip()
    if not secret:
        raise HTTPException(status_code=503, detail="INTEGRATION_WEBHOOK_SECRET is not configured")

    timestamp_raw = request.headers.get("X-Feature-Factory-Timestamp", "").strip()
    signature = request.headers.get("X-Feature-Factory-Signature", "").strip()
    if not timestamp_raw or not signature:
        raise HTTPException(status_code=401, detail="Missing signature headers")

    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp header")

    now = int(time.time())
    if abs(now - timestamp) > settings.integration_webhook_ttl_seconds:
        raise HTTPException(status_code=401, detail="Callback signature expired")

    signed_payload = f"{timestamp_raw}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    expected_header = f"sha256={expected}"
    if not hmac.compare_digest(signature, expected_header):
        raise HTTPException(status_code=401, detail="Invalid callback signature")


def _transition_feature(feature: FeatureRequest, action: str) -> None:
    result = perform_action(feature.status, action)
    if result.new_status != feature.status:
        validate_transition(feature.status, result.new_status)
        feature.status = result.new_status


def _apply_execution_callback(feature: FeatureRequest, payload: ExecutionCallbackIn) -> None:
    event = payload.event

    if event == "pr_opened":
        if feature.status == BUILDING:
            _transition_feature(feature, "opened_pr")
        elif feature.status != PR_OPENED:
            raise ValueError(f"Cannot apply pr_opened in status {feature.status}")
        if payload.github_pr_url:
            feature.github_pr_url = payload.github_pr_url
        return

    if event == "preview_ready":
        if feature.status == BUILDING:
            _transition_feature(feature, "opened_pr")
        if feature.status == PR_OPENED:
            _transition_feature(feature, "preview_ready")
        elif feature.status != PREVIEW_READY:
            raise ValueError(f"Cannot apply preview_ready in status {feature.status}")
        if payload.github_pr_url:
            feature.github_pr_url = payload.github_pr_url
        if payload.preview_url:
            feature.preview_url = payload.preview_url
        return

    if event == "build_failed":
        if feature.status in {BUILDING, PR_OPENED}:
            _transition_feature(feature, "fail_build")
        elif feature.status != FAILED_BUILD:
            raise ValueError(f"Cannot apply build_failed in status {feature.status}")
        feature.last_error = payload.message or "External build reported failure"
        return

    if event == "preview_failed":
        if feature.status == PR_OPENED:
            _transition_feature(feature, "fail_preview")
        elif feature.status != FAILED_PREVIEW:
            raise ValueError(f"Cannot apply preview_failed in status {feature.status}")
        feature.last_error = payload.message or "External preview reported failure"
        return

    raise ValueError(f"Unsupported callback event: {event}")


@router.get("/feature-requests", response_model=list[FeatureRequestOut])
def list_feature_requests(db: Session = Depends(get_db)):
    rows = db.execute(select(FeatureRequest).order_by(FeatureRequest.created_at.desc())).scalars().all()
    return [_feature_to_out(r) for r in rows]


@router.post("/feature-requests", response_model=FeatureRequestOut)
def create_feature(
    payload: FeatureRequestCreate,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_auth),
):
    feature = create_feature_request(db, payload)
    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)


@router.get("/feature-requests/{feature_id}", response_model=FeatureRequestOut)
def get_feature(feature_id: str, db: Session = Depends(get_db)):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")
    return _feature_to_out(feature)


@router.post("/feature-requests/{feature_id}/revalidate", response_model=FeatureRequestOut)
def revalidate_spec(
    feature_id: str,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_auth),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        refresh_spec_validation(db, feature)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)


@router.patch("/feature-requests/{feature_id}/spec", response_model=FeatureRequestOut)
def patch_spec(
    feature_id: str,
    payload: FeatureSpecUpdateRequest,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_auth),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        update_feature_spec(db, feature, payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)


@router.post("/feature-requests/{feature_id}/build")
def start_build(
    feature_id: str,
    payload: BuildRequest | None = None,
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_auth),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")

    if feature.status != READY_FOR_BUILD:
        raise HTTPException(status_code=400, detail=f"Feature must be READY_FOR_BUILD (currently {feature.status})")

    q = get_queue()
    try:
        job = q.enqueue(kickoff_build_job, feature.id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to enqueue build: {e}")

    log_event(
        db,
        feature,
        event_type="build_enqueued",
        actor_type=(payload.actor_type if payload else "user"),
        actor_id=(payload.actor_id if payload else ""),
        message=(payload.message if payload else "Build enqueued"),
        data={"job_id": job.id},
    )
    db.commit()

    return {"ok": True, "enqueued": True, "feature_id": feature.id, "job_id": job.id}


@router.post("/feature-requests/{feature_id}/approve", response_model=FeatureRequestOut)
def approve(
    feature_id: str,
    approver: str = "local-user",
    db: Session = Depends(get_db),
    _auth: None = Depends(require_api_auth),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        feature = mark_product_approved(db, feature, approver=approver)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)


@router.post("/integrations/execution-callback", response_model=FeatureRequestOut)
async def execution_callback(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    _verify_execution_callback_signature(request, raw_body)

    try:
        payload = ExecutionCallbackIn.model_validate_json(raw_body)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid callback payload: {e}")

    feature = db.get(FeatureRequest, payload.feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    try:
        _apply_execution_callback(feature, payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_event(
        db,
        feature,
        event_type=f"integration_{payload.event}",
        actor_type="system",
        actor_id=payload.actor_id or "integration",
        message=payload.message or f"Execution callback: {payload.event}",
        data={
            "event": payload.event,
            "github_pr_url": payload.github_pr_url,
            "preview_url": payload.preview_url,
            "metadata": payload.metadata,
        },
    )

    if payload.event == "preview_ready":
        slack = get_slack_adapter()
        if notify_reviewer_for_approval(feature, slack):
            log_event(
                db,
                feature,
                event_type="reviewer_notified",
                actor_type="system",
                actor_id="integration",
                message="Reviewer/admin notified for approval",
            )

    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)
