from __future__ import annotations

import hashlib
import hmac
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import FeatureRequest, IntegrationCallbackReceipt
from app.queue import get_queue
from app.security import (
    AuthenticatedUser,
    require_authenticated_user,
    require_can_approve,
    require_can_build,
    require_can_request_or_update_spec,
    user_can_access_feature,
    user_can_view_all_features,
)
from app.schemas import (
    BuildRequest,
    ExecutionCallbackIn,
    FeatureRequestCreate,
    FeatureRequestListOut,
    FeatureRequestOut,
    FeatureSpecUpdateRequest,
)
from app.services.event_logger import log_event
from app.services.feature_service import (
    BuildAlreadyInProgressError,
    create_feature_request,
    mark_product_approved,
    refresh_spec_validation,
    transition_feature_to_building,
    update_feature_spec,
)
from app.services.github_adapter import get_github_adapter
from app.services.pr_description import build_standard_pr_body
from app.services.reviewer_service import notify_reviewer_for_approval
from app.services.slack_adapter import get_slack_adapter
from app.services.prompt_optimizer import detect_ui_feature
from app.services.url_safety import normalize_external_url
from app.state_machine import (
    FAILED_BUILD,
    FAILED_SPEC,
    FAILED_PREVIEW,
    PREVIEW_READY,
    PR_OPENED,
    BUILDING,
    NEEDS_INFO,
    READY_FOR_BUILD,
    perform_action,
    validate_transition,
)
from app.tasks.jobs import kickoff_build_job

router = APIRouter()
integration_router = APIRouter()
PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def _feature_to_out(feature: FeatureRequest, *, include_events: bool = True) -> FeatureRequestOut:
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
        active_build_job_id=feature.active_build_job_id,
        product_approved_by=feature.product_approved_by,
        product_approved_at=feature.product_approved_at,
        last_error=feature.last_error,
        events=[] if not include_events else [
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
    validate_transition(feature.status, result.new_status)
    feature.status = result.new_status


def _assert_receipt_payload_match(*, existing: IntegrationCallbackReceipt, payload_hash: str) -> None:
    if existing.payload_hash == payload_hash:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Idempotency key already used with different payload",
            "idempotency_key": existing.idempotency_key,
        },
    )


def _build_idempotent_payload(feature: FeatureRequest, *, job_id: str = "") -> dict[str, object]:
    resolved_job_id = (job_id or feature.active_build_job_id or "").strip()
    return {
        "ok": True,
        "enqueued": False,
        "already_in_progress": True,
        "feature_id": feature.id,
        "job_id": resolved_job_id,
        "status": feature.status,
    }


def _build_invalid_detail(feature: FeatureRequest) -> dict[str, object]:
    spec = feature.spec or {}
    validation = spec.get("_validation") or {}
    missing = [str(x).strip() for x in (validation.get("missing") or []) if str(x).strip()]
    detail: dict[str, object] = {
        "message": f"Feature is not ready to build from status {feature.status}",
        "feature_id": feature.id,
        "status": feature.status,
    }
    if missing:
        detail["missing"] = missing
        detail["next_action"] = "Provide missing fields and retry build."
    if feature.status in {NEEDS_INFO, FAILED_SPEC} and not missing:
        detail["next_action"] = "Update spec details and revalidate before build."
    return detail


def _apply_execution_callback(feature: FeatureRequest, payload: ExecutionCallbackIn) -> None:
    event = payload.event

    if event == "pr_opened":
        if feature.status == BUILDING:
            _transition_feature(feature, "opened_pr")
        elif feature.status != PR_OPENED:
            raise ValueError(f"Cannot apply pr_opened in status {feature.status}")
        if payload.github_pr_url:
            safe_pr_url = normalize_external_url(payload.github_pr_url)
            if safe_pr_url:
                feature.github_pr_url = safe_pr_url
        return

    if event == "preview_ready":
        if feature.status == BUILDING:
            _transition_feature(feature, "opened_pr")
        if feature.status == PR_OPENED:
            _transition_feature(feature, "preview_ready")
        elif feature.status != PREVIEW_READY:
            raise ValueError(f"Cannot apply preview_ready in status {feature.status}")
        if payload.github_pr_url:
            safe_pr_url = normalize_external_url(payload.github_pr_url)
            if safe_pr_url:
                feature.github_pr_url = safe_pr_url
        if payload.preview_url:
            safe_preview_url = normalize_external_url(payload.preview_url)
            if safe_preview_url:
                feature.preview_url = safe_preview_url
        feature.active_build_job_id = ""
        return

    if event == "build_failed":
        if feature.status in {BUILDING, PR_OPENED}:
            _transition_feature(feature, "fail_build")
        elif feature.status != FAILED_BUILD:
            raise ValueError(f"Cannot apply build_failed in status {feature.status}")
        feature.last_error = payload.message or "External build reported failure"
        feature.active_build_job_id = ""
        return

    if event == "preview_failed":
        if feature.status == PR_OPENED:
            _transition_feature(feature, "fail_preview")
        elif feature.status != FAILED_PREVIEW:
            raise ValueError(f"Cannot apply preview_failed in status {feature.status}")
        feature.last_error = payload.message or "External preview reported failure"
        feature.active_build_job_id = ""
        return

    raise ValueError(f"Unsupported callback event: {event}")


def _callback_status_text(feature: FeatureRequest, payload: ExecutionCallbackIn) -> str:
    if payload.event == "pr_opened":
        return (
            f"Status update for *{feature.title}*: `PR_OPENED`\n"
            f"PR: {feature.github_pr_url or payload.github_pr_url or '(pending)'}"
        )
    if payload.event == "preview_ready":
        return (
            f"Status update for *{feature.title}*: `PREVIEW_READY`\n"
            f"PR: {feature.github_pr_url or payload.github_pr_url or '(pending)'}\n"
            f"Preview: {feature.preview_url or payload.preview_url or '(pending)'}"
        )
    if payload.event == "build_failed":
        return (
            f"Status update for *{feature.title}*: `FAILED_BUILD`\n"
            f"Error: {payload.message or feature.last_error or 'External build failed.'}"
        )
    if payload.event == "preview_failed":
        return (
            f"Status update for *{feature.title}*: `FAILED_PREVIEW`\n"
            f"Error: {payload.message or feature.last_error or 'External preview failed.'}"
        )
    return f"Status update for *{feature.title}*: `{feature.status}`"


def _extract_pr_number(pr_url: str) -> int:
    match = PR_NUMBER_RE.search((pr_url or "").strip())
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _extract_issue_number(issue_url: str) -> int:
    text = (issue_url or "").strip().rstrip("/")
    if not text:
        return 0
    tail = text.rsplit("/", 1)[-1]
    if not tail.isdigit():
        return 0
    return int(tail)


async def _sync_standard_pr_body(feature: FeatureRequest) -> None:
    pr_url = (feature.github_pr_url or "").strip()
    if not pr_url:
        return

    pr_number = _extract_pr_number(pr_url)
    if pr_number <= 0:
        return

    settings = get_settings()
    runner_metadata: dict[str, object] = {}
    for event in sorted(feature.events, key=lambda x: x.created_at, reverse=True):
        if event.event_type != "coderunner_completed":
            continue
        event_data = event.data or {}
        maybe_meta = event_data.get("runner_metadata")
        if isinstance(maybe_meta, dict):
            runner_metadata = maybe_meta
            break

    summary = str(runner_metadata.get("assistant_summary") or "").strip()
    if not summary:
        summary = f"Automated implementation for {feature.title}."
        if feature.preview_url:
            summary = f"Automated implementation for {feature.title} with preview deployment."

    verification_output = str(runner_metadata.get("verification_output") or "").strip()
    if not verification_output:
        verification_output = feature.last_error or ""

    verification_command = str(runner_metadata.get("verification_command") or "").strip()
    if not verification_command:
        verification_command = (settings.llm_test_command or "").strip()

    verification_warning = str(runner_metadata.get("verification_warning") or "").strip()
    branch_name = str(runner_metadata.get("branch_name") or "").strip() or "(managed by external runner)"
    runner_name = (
        str(runner_metadata.get("execution_mode") or "").strip()
        or str(runner_metadata.get("runner") or "").strip()
        or "opencode-delegated"
    )
    runner_model = str(runner_metadata.get("model") or "").strip() or (settings.opencode_model or "").strip()

    spec = dict(feature.spec or {})
    ui_feature, _ui_keywords = detect_ui_feature(spec)
    if bool(spec.get("ui_feature")) or ui_feature:
        spec["ui_feature"] = True

    body = build_standard_pr_body(
        spec=spec,
        feature_id=feature.id,
        issue_number=_extract_issue_number(feature.github_issue_url),
        branch_name=branch_name,
        runner_name=runner_name,
        runner_model=runner_model,
        summary=summary,
        verification_output=verification_output,
        verification_command=verification_command,
        verification_warning=verification_warning,
        preview_url=feature.preview_url or "",
        cloudflare_project_name=settings.cloudflare_pages_project_name,
        cloudflare_production_branch=settings.cloudflare_pages_production_branch,
        repo_path=None,
    )
    github = get_github_adapter()
    await github.update_pull_request_body(pr_number=pr_number, body=body)


@router.get("/feature-requests", response_model=FeatureRequestListOut)
def list_feature_requests(
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_authenticated_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str = Query(default=""),
    mine: bool = Query(default=True),
    include_events: bool = Query(default=False),
):
    conditions = []
    status_filter = status.strip()
    if status_filter:
        conditions.append(FeatureRequest.status == status_filter)

    can_view_all = user_can_view_all_features(user)
    if mine or not can_view_all:
        identities = sorted(user.identity_candidates())
        if not identities:
            return FeatureRequestListOut(items=[], total=0, limit=limit, offset=offset, has_more=False)
        conditions.append(FeatureRequest.requester_user_id.in_(identities))

    total_stmt = select(func.count()).select_from(FeatureRequest)
    if conditions:
        total_stmt = total_stmt.where(*conditions)
    total = int(db.execute(total_stmt).scalar_one() or 0)

    stmt = select(FeatureRequest)
    if conditions:
        stmt = stmt.where(*conditions)
    rows = (
        db.execute(stmt.order_by(FeatureRequest.created_at.desc()).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    items = [_feature_to_out(r, include_events=include_events) for r in rows]
    return FeatureRequestListOut(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(items)) < total,
    )


@router.post("/feature-requests", response_model=FeatureRequestOut)
def create_feature(
    payload: FeatureRequestCreate,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_can_request_or_update_spec),
):
    requester = (payload.requester_user_id or "").strip()
    if user.auth_source != "api_token" or not requester:
        requester = user.actor_id
    payload = payload.model_copy(update={"requester_user_id": requester})

    feature = create_feature_request(db, payload)
    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)


@router.get("/feature-requests/{feature_id}", response_model=FeatureRequestOut)
def get_feature(
    feature_id: str,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_authenticated_user),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")
    if not user_can_access_feature(user, feature.requester_user_id):
        raise HTTPException(status_code=403, detail="Not allowed to view this feature")
    return _feature_to_out(feature)


@router.post("/feature-requests/{feature_id}/revalidate", response_model=FeatureRequestOut)
def revalidate_spec(
    feature_id: str,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_can_request_or_update_spec),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")
    if not user_can_access_feature(user, feature.requester_user_id):
        raise HTTPException(status_code=403, detail="Not allowed to revalidate this feature")

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
    user: AuthenticatedUser = Depends(require_can_request_or_update_spec),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")
    if not user_can_access_feature(user, feature.requester_user_id):
        raise HTTPException(status_code=403, detail="Not allowed to update this feature")

    try:
        actor_id = (payload.actor_id or "").strip()
        if user.auth_source != "api_token" or not actor_id:
            actor_id = user.actor_id
        payload = payload.model_copy(update={"actor_id": actor_id})
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
    user: AuthenticatedUser = Depends(require_can_build),
):
    feature = (
        db.execute(select(FeatureRequest).where(FeatureRequest.id == feature_id).with_for_update())
        .scalars()
        .first()
    )
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")

    build_payload = payload or BuildRequest()
    actor_id = (build_payload.actor_id or "").strip()
    if user.auth_source != "api_token" or not actor_id:
        actor_id = user.actor_id

    if feature.status == BUILDING:
        payload_data = _build_idempotent_payload(feature)
        log_event(
            db,
            feature,
            event_type="build_enqueue_reused",
            actor_type=build_payload.actor_type or "user",
            actor_id=actor_id,
            message=build_payload.message or "Build already running; reused existing job",
            data={"job_id": payload_data.get("job_id", "")},
        )
        db.commit()
        return payload_data
    if feature.status != READY_FOR_BUILD:
        raise HTTPException(status_code=400, detail=_build_invalid_detail(feature))

    try:
        transition_feature_to_building(feature)
    except BuildAlreadyInProgressError as e:
        payload_data = _build_idempotent_payload(feature, job_id=e.job_id)
        log_event(
            db,
            feature,
            event_type="build_enqueue_reused",
            actor_type=build_payload.actor_type or "user",
            actor_id=actor_id,
            message=build_payload.message or "Build already running; reused existing job",
            data={"job_id": payload_data.get("job_id", "")},
        )
        db.commit()
        return payload_data
    except ValueError:
        raise HTTPException(status_code=400, detail=_build_invalid_detail(feature))

    q = get_queue()
    try:
        job = q.enqueue(kickoff_build_job, feature.id)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Failed to enqueue build: {e}")

    feature.active_build_job_id = job.id
    log_event(
        db,
        feature,
        event_type="build_enqueued",
        actor_type=build_payload.actor_type or "user",
        actor_id=actor_id,
        message=build_payload.message or "Build enqueued",
        data={"job_id": job.id},
    )
    db.commit()

    return {
        "ok": True,
        "enqueued": True,
        "already_in_progress": False,
        "feature_id": feature.id,
        "job_id": job.id,
        "status": feature.status,
    }


@router.post("/feature-requests/{feature_id}/approve", response_model=FeatureRequestOut)
def approve(
    feature_id: str,
    approver: str = "",
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_can_approve),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404, detail="Not found")

    effective_approver = (approver or "").strip()
    if user.auth_source != "api_token" or not effective_approver:
        effective_approver = user.actor_id

    try:
        feature = mark_product_approved(db, feature, approver=effective_approver, preauthorized=True)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)


@integration_router.post("/integrations/execution-callback", response_model=FeatureRequestOut)
async def execution_callback(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    _verify_execution_callback_signature(request, raw_body)

    try:
        payload = ExecutionCallbackIn.model_validate_json(raw_body)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"Invalid callback payload: {e}")

    idempotency_key = (
        (request.headers.get("X-Feature-Factory-Event-Id") or "").strip()
        or (payload.event_id or "").strip()
    )
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Missing callback idempotency key")

    payload_hash = hashlib.sha256(raw_body).hexdigest()
    existing_receipt = db.get(IntegrationCallbackReceipt, idempotency_key)
    if existing_receipt:
        _assert_receipt_payload_match(existing=existing_receipt, payload_hash=payload_hash)
        feature = db.get(FeatureRequest, existing_receipt.feature_id)
        if not feature:
            raise HTTPException(status_code=409, detail="Idempotency key already used for missing feature")
        return _feature_to_out(feature)

    feature = None
    if (payload.feature_id or "").strip():
        feature = db.get(FeatureRequest, payload.feature_id)
    elif (payload.github_pr_url or "").strip():
        normalized_pr = normalize_external_url(payload.github_pr_url)
        if normalized_pr:
            feature = (
                db.execute(
                    select(FeatureRequest)
                    .where(FeatureRequest.github_pr_url == normalized_pr)
                    .order_by(FeatureRequest.updated_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
    if feature is None and (payload.github_pr_url or "").strip():
        # Best-effort fallback: match by issue number embedded in metadata.
        issue_number = 0
        try:
            issue_number = int(str((payload.metadata or {}).get("issue_number") or "0") or "0")
        except ValueError:
            issue_number = 0
        if issue_number > 0:
            feature = (
                db.execute(
                    select(FeatureRequest)
                    .where(FeatureRequest.github_issue_url.like(f"%/issues/{issue_number}"))
                    .order_by(FeatureRequest.updated_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    db.add(
        IntegrationCallbackReceipt(
            idempotency_key=idempotency_key,
            feature_id=feature.id,
            event_type=payload.event,
            payload_hash=payload_hash,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing_receipt = db.get(IntegrationCallbackReceipt, idempotency_key)
        if existing_receipt:
            _assert_receipt_payload_match(existing=existing_receipt, payload_hash=payload_hash)
        feature = db.get(FeatureRequest, payload.feature_id)
        if not feature:
            raise HTTPException(status_code=409, detail="Duplicate callback with missing feature")
        return _feature_to_out(feature)

    try:
        _apply_execution_callback(feature, payload)
    except Exception as e:
        db.rollback()
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
            "event_id": idempotency_key,
            "github_pr_url": payload.github_pr_url,
            "preview_url": payload.preview_url,
            "metadata": payload.metadata,
        },
    )

    slack = get_slack_adapter()
    if feature.slack_channel_id and feature.slack_thread_ts:
        slack.post_thread_message(
            channel=feature.slack_channel_id,
            thread_ts=feature.slack_thread_ts,
            text=_callback_status_text(feature, payload),
        )

    if payload.event == "preview_ready":
        if notify_reviewer_for_approval(feature, slack):
            log_event(
                db,
                feature,
                event_type="reviewer_notified",
                actor_type="system",
                actor_id="integration",
                message="Reviewer/admin notified for approval",
            )

    if payload.event in {"pr_opened", "preview_ready"} and feature.github_pr_url:
        try:
            await _sync_standard_pr_body(feature)
        except Exception as e:  # noqa: BLE001
            log_event(
                db,
                feature,
                event_type="pr_body_sync_failed",
                actor_type="system",
                actor_id="integration",
                message=f"Failed to sync standardized PR body: {e}",
            )

    db.commit()
    db.refresh(feature)
    return _feature_to_out(feature)
