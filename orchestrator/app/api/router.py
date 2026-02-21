from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.routes import api, health, ui
from app.security import require_authenticated_user

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(ui.router, tags=["ui"], dependencies=[Depends(require_authenticated_user)])
api_router.include_router(api.router, prefix="/api", tags=["api"], dependencies=[Depends(require_authenticated_user)])
api_router.include_router(api.integration_router, prefix="/api", tags=["api"])
