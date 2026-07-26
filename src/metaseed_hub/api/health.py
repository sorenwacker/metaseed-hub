"""Health check endpoint."""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from metaseed_hub._version import __version__
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
        "version": __version__,
        "services": {},
    }

    # Check database
    engine = None
    try:
        engine = create_async_engine(settings.database_url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["services"]["database"] = "healthy"
    except Exception as e:
        status["services"]["database"] = f"unhealthy: {e}"
        status["status"] = "degraded"
    finally:
        # Dispose even on failure, or the engine's connection pool leaks on
        # every unhealthy poll.
        if engine is not None:
            await engine.dispose()

    # Check Redis
    client = None
    try:
        import redis.asyncio as redis

        client = redis.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
        await client.ping()
        status["services"]["redis"] = "healthy"
    except Exception as e:
        status["services"]["redis"] = f"unhealthy: {e}"
        status["status"] = "degraded"
    finally:
        if client is not None:
            await client.aclose()

    return status


@router.get("/ready")
async def readiness_check() -> dict[str, str]:
    """Simple readiness check.

    Returns:
        Dictionary indicating the application is ready.
    """
    return {"status": "ready"}
