"""Health check endpoint."""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from metaseed_hub.config import get_settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Check application health status.

    Returns:
        Dictionary with health status of all services.
    """
    settings = get_settings()
    status: dict[str, Any] = {
        "status": "healthy",
        "services": {},
    }

    # Check database
    try:
        engine = create_async_engine(settings.database_url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["services"]["database"] = "healthy"
        await engine.dispose()
    except Exception as e:
        status["services"]["database"] = f"unhealthy: {e}"
        status["status"] = "degraded"

    # Check Redis
    try:
        import redis.asyncio as redis

        client = redis.from_url(settings.redis_url)
        await client.ping()
        status["services"]["redis"] = "healthy"
        await client.aclose()
    except Exception as e:
        status["services"]["redis"] = f"unhealthy: {e}"
        status["status"] = "degraded"

    return status


@router.get("/ready")
async def readiness_check() -> dict[str, str]:
    """Simple readiness check.

    Returns:
        Dictionary indicating the application is ready.
    """
    return {"status": "ready"}
