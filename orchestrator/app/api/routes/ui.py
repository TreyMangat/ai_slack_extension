from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import FeatureRequest
from app.queue import get_queue
from app.schemas import FeatureRequestCreate, FeatureSpec, FeatureSpecPatch, FeatureSpecUpdateRequest
from app.services.feature_service import create_feature_request, update_feature_spec
from app.state_machine import READY_FOR_BUILD, PREVIEW_READY
from app.tasks.jobs import kickoff_build_job

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    features = db.execute(select(FeatureRequest).order_by(FeatureRequest.created_at.desc())).scalars().all()
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

    payload = FeatureRequestCreate(spec=spec, requester_user_id="local-user")
    feature = create_feature_request(db, payload)
    db.commit()

    return RedirectResponse(url=f"/features/{feature.id}", status_code=303)


@router.get("/features/{feature_id}", response_class=HTMLResponse)
def feature_detail(feature_id: str, request: Request, db: Session = Depends(get_db)):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404)

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
def build_from_ui(feature_id: str, db: Session = Depends(get_db)):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404)

    if feature.status != READY_FOR_BUILD:
        raise HTTPException(status_code=400, detail=f"Not ready for build ({feature.status})")

    q = get_queue()
    try:
        q.enqueue(kickoff_build_job, feature.id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to enqueue build: {e}")

    return RedirectResponse(url=f"/features/{feature.id}", status_code=303)


@router.post("/features/{feature_id}/approve")
def approve_from_ui(feature_id: str, db: Session = Depends(get_db)):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404)

    from app.services.feature_service import mark_product_approved

    try:
        mark_product_approved(db, feature, approver="local-user")
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
):
    feature = db.get(FeatureRequest, feature_id)
    if not feature:
        raise HTTPException(status_code=404)

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
        actor_id="local-user",
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
