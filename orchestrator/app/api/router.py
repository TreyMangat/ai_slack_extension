from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import api, health, ui

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(ui.router, tags=["ui"])
api_router.include_router(api.router, prefix="/api", tags=["api"])
