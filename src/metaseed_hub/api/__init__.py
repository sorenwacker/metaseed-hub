"""API router registration."""

from fastapi import APIRouter

from metaseed_hub.api.datasets import router as datasets_router
from metaseed_hub.api.health import router as health_router

api_router = APIRouter()

api_router.include_router(health_router, tags=["health"])
api_router.include_router(datasets_router, prefix="/datasets", tags=["datasets"])

__all__ = ["api_router"]
