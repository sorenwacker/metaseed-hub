"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from metaseed.ui.app import STATIC_DIR as METASEED_STATIC

from metaseed_hub import __version__
from metaseed_hub.api import api_router
from metaseed_hub.auth import verify_token
from metaseed_hub.config import get_settings
from metaseed_hub.database import db
from metaseed_hub.websocket import manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler.

    Args:
        app: FastAPI application instance.

    Yields:
        None after startup, cleans up on shutdown.
    """
    settings = get_settings()

    # Startup
    await db.connect(settings.database_url, echo=settings.debug)
    await manager.connect_redis()

    yield

    # Shutdown
    await manager.disconnect_redis()
    await db.disconnect()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application.
    """
    settings = get_settings()

    app = FastAPI(
        title="Metaseed Hub",
        description="Collaborative hub for metaseed projects",
        version=__version__,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount metaseed static files
    app.mount("/static", StaticFiles(directory=str(METASEED_STATIC)), name="static")

    # Include API routes
    app.include_router(api_router, prefix="/api")

    # Include ontology API at /api/ontology for metaseed lookup.js compatibility
    from metaseed_hub.ui.routes.ontology_api import router as ontology_router

    app.include_router(ontology_router)

    # Include Hub UI routes
    from metaseed_hub.ui.app import create_hub_app

    hub_app = create_hub_app()
    app.mount("/hub", hub_app)

    # Redirect root to hub
    from fastapi.responses import RedirectResponse

    @app.get("/")
    async def root() -> RedirectResponse:
        """Redirect to hub UI."""
        return RedirectResponse(url="/hub/")

    @app.get("/version")
    async def version() -> dict[str, str]:
        """Return version information."""
        return {"version": __version__}

    # WebSocket endpoint
    @app.websocket("/ws/{project_id}")
    async def websocket_endpoint(
        websocket: WebSocket,
        project_id: str,
        token: Annotated[str, Query()],
    ) -> None:
        """WebSocket endpoint for real-time project collaboration.

        Args:
            websocket: WebSocket connection.
            project_id: Project identifier.
            token: JWT access token for authentication.
        """
        from metaseed_hub.ui.dependencies import get_dataset_for_user

        try:
            user = await verify_token(token)
        except Exception:
            await websocket.close(code=4001)
            return

        # Authorize the room: the user must have access to the project
        # (dataset) through their tenant or an explicit DatasetMember grant,
        # mirroring the HTTP routes. Without this any authenticated user
        # could join any project's room and read its messages and presence.
        try:
            async for session in db.session():
                await get_dataset_for_user(project_id, session, user)
                break
        except Exception:
            await websocket.close(code=4003)
            return

        try:
            await manager.handle_connection(
                websocket=websocket,
                project_id=project_id,
                user_id=user.keycloak_id,
                user_name=user.name,
            )
        except Exception:
            await websocket.close(code=4001)

    return app


app = create_app()
