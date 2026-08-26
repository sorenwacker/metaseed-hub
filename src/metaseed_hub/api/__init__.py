"""API router registration."""

from fastapi import APIRouter

from metaseed_hub.api.datasets import router as datasets_router
from metaseed_hub.api.health import router as health_router
from metaseed_hub.api.me import router as me_router
from metaseed_hub.api.specs import router as specs_router

api_router = APIRouter()

api_router.include_router(health_router, tags=["health"])
api_router.include_router(datasets_router, prefix="/datasets", tags=["datasets"])
api_router.include_router(me_router, prefix="/me", tags=["me"])
api_router.include_router(specs_router, prefix="/specs", tags=["specs"])

__all__ = ["api_router"]
