from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import FeatureRequest
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
from app.schemas import FeatureRequestCreate, FeatureSpec, FeatureSpecPatch, FeatureSpecUpdateRequest
from app.services.feature_service import (
    BuildAlreadyInProgressError,
    create_feature_request,
    transition_feature_to_building,
    update_feature_spec,
)
from app.state_machine import READY_FOR_BUILD, PREVIEW_READY
from app.tasks.jobs import kickoff_build_job

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_authenticated_user),
):
    stmt = select(FeatureRequest)
    if not user_can_view_all_features(user):
        stmt = stmt.where(FeatureRequest.requester_user_id.in_(sorted(user.identity_candidates())))
    features = db.execute(stmt.order_by(FeatureRequest.created_at.desc())).scalars().all()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "features": features,
        },
    )


@router.post("/create")
def create_from_form(
    title: str = Form(...),
    problem: str = Form(...),
    business_justification: str = Form(""),
    implementation_mode: str = Form("new_feature"),
    source_repos: str = Form(""),
    acceptance_criteria: str = Form(""),
    proposed_solution: str = Form(""),
    non_goals: str = Form(""),
    repo: str = Form(""),
    risk_flags: str = Form(""),
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_can_request_or_update_spec),
):
    ac_list = [line.strip() for line in acceptance_criteria.splitlines() if line.strip()]
    ng_list = [line.strip() for line in non_goals.splitlines() if line.strip()]
    rf_list = [x.strip() for x in risk_flags.split(",") if x.strip()]
    source_repo_list = [line.strip() for line in source_repos.splitlines() if line.strip()]
    mode = implementation_mode.strip() if implementation_mode in {"new_feature", "reuse_existing"} else "new_feature"

    spec = FeatureSpec(
        title=title.strip(),
        problem=problem.strip(),
        business_justification=business_justification.strip(),
        implementation_mode=mode,
        source_repos=source_repo_list,
        proposed_solution=proposed_solution.strip(),
        acceptance_criteria=ac_list,
        non_goals=ng_list,
        repo=repo.strip(),
        risk_flags=rf_list,
    )

    payload = FeatureRequestCreate(spec=spec, requester_user_id=user.actor_id)
    feature = create_feature_request(db, payload)
    db.commit()

    return RedirectResponse(url=f"/features/{feature.id}", status_code=303)


@router.get("/features/{feature_id}", response_class=HTMLResponse)
def feature_detail(
    feature_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_authenticated_user),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404)
    if not user_can_access_feature(user, feature.requester_user_id):
        raise HTTPException(status_code=403)

    events = sorted(feature.events, key=lambda e: e.created_at)

    return templates.TemplateResponse(
        "feature_detail.html",
        {
            "request": request,
            "feature": feature,
            "events": events,
            "ready_for_build": feature.status == READY_FOR_BUILD,
            "can_approve": feature.status == PREVIEW_READY,
        },
    )


@router.post("/features/{feature_id}/build")
def build_from_ui(
    feature_id: str,
    db: Session = Depends(get_db),
    _user: AuthenticatedUser = Depends(require_can_build),
):
    feature = (
        db.execute(select(FeatureRequest).where(FeatureRequest.id == feature_id).with_for_update())
        .scalars()
        .first()
    )
    if not feature:
        raise HTTPException(status_code=404)

    try:
        transition_feature_to_building(feature)
    except BuildAlreadyInProgressError:
        return RedirectResponse(url=f"/features/{feature.id}", status_code=303)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    q = get_queue()
    try:
        job = q.enqueue(kickoff_build_job, feature.id)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Failed to enqueue build: {e}")

    feature.active_build_job_id = job.id
    db.commit()
    return RedirectResponse(url=f"/features/{feature.id}", status_code=303)


@router.post("/features/{feature_id}/approve")
def approve_from_ui(
    feature_id: str,
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_can_approve),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404)

    from app.services.feature_service import mark_product_approved

    try:
        mark_product_approved(db, feature, approver=user.actor_id, preauthorized=True)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()

    return RedirectResponse(url=f"/features/{feature.id}", status_code=303)


@router.post("/features/{feature_id}/update")
def update_from_ui(
    feature_id: str,
    title: str = Form(""),
    problem: str = Form(""),
    business_justification: str = Form(""),
    implementation_mode: str = Form("new_feature"),
    source_repos: str = Form(""),
    acceptance_criteria: str = Form(""),
    proposed_solution: str = Form(""),
    non_goals: str = Form(""),
    repo: str = Form(""),
    risk_flags: str = Form(""),
    links: str = Form(""),
    db: Session = Depends(get_db),
    user: AuthenticatedUser = Depends(require_can_request_or_update_spec),
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404)
    if not user_can_access_feature(user, feature.requester_user_id):
        raise HTTPException(status_code=403)

    ac_list = [line.strip() for line in acceptance_criteria.splitlines() if line.strip()]
    ng_list = [line.strip() for line in non_goals.splitlines() if line.strip()]
    source_repo_list = [line.strip() for line in source_repos.splitlines() if line.strip()]
    link_list = [line.strip() for line in links.splitlines() if line.strip()]
    rf_list = [x.strip() for x in risk_flags.split(",") if x.strip()]
    mode = implementation_mode.strip() if implementation_mode in {"new_feature", "reuse_existing"} else "new_feature"

    patch = FeatureSpecPatch(
        title=title,
        problem=problem,
        business_justification=business_justification,
        implementation_mode=mode,
        source_repos=source_repo_list,
        proposed_solution=proposed_solution,
        acceptance_criteria=ac_list,
        non_goals=ng_list,
        repo=repo,
        risk_flags=rf_list,
        links=link_list,
    )
    payload = FeatureSpecUpdateRequest(
        spec=patch,
        actor_type="user",
        actor_id=user.actor_id,
        message="Spec updated from local UI",
    )

    try:
        update_feature_spec(db, feature, payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    return RedirectResponse(url=f"/features/{feature.id}", status_code=303)


@router.get("/preview/{preview_id}", response_class=HTMLResponse)
def preview_page(preview_id: str, request: Request):
    return templates.TemplateResponse(
        "preview.html",
        {
            "request": request,
            "preview_id": preview_id,
        },
    )
