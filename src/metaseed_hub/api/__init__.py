"""API router registration."""

from fastapi import APIRouter

from metaseed_hub.api.health import router as health_router
from metaseed_hub.api.projects import router as projects_router

api_router = APIRouter()

api_router.include_router(health_router, tags=["health"])
api_router.include_router(projects_router, prefix="/projects", tags=["projects"])

__all__ = ["api_router"]
