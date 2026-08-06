"""FastAPI application entry point."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from metaseed_hub import __version__
from metaseed_hub.api import api_router
from metaseed_hub.auth import verify_token
from metaseed_hub.config import get_settings
from metaseed_hub.database import db
from metaseed_hub.ui.metaseed_ui import METASEED_STATIC_DIR as METASEED_STATIC
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
    if settings.using_default_secret_key and not settings.debug:
        logging.getLogger("metaseed_hub").warning(
            "SECRET_KEY is the built-in default in a non-debug deployment; CSRF "
            "tokens are forgeable. Set SECRET_KEY (openssl rand -base64 48)."
        )
    await db.connect(settings.database_url, echo=settings.debug)
    await manager.connect_redis()

    # The MCP app is mounted, and a mounted sub-app's lifespan is not run by the
    # parent. Its session manager therefore has to be started here, or every
    # request to /hub/mcp fails with an uninitialised task group.
    mcp_server = getattr(app.state, "mcp_server", None)
    if mcp_server is None:
        yield
    else:
        async with mcp_server.session_manager.run():
            yield

    # Shutdown
    await manager.disconnect_redis()
    await db.disconnect()


MCP_PATH = "/hub/mcp"


class _AcceptMcpWithoutTrailingSlash:
    """Let ``/hub/mcp`` reach the MCP app, not just ``/hub/mcp/``.

    Starlette's ``Mount`` only matches when the remainder of the path begins
    with a slash, so a request to the mount point exactly falls through to a
    404. Clients are configured with the bare URL, and a 404 on the documented
    address is not a failure anyone can diagnose.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == MCP_PATH:
            scope = {**scope, "path": MCP_PATH + "/"}
        await self.app(scope, receive, send)


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

    # Mounted before /hub, and outside create_hub_app, deliberately. The hub app
    # applies a same-origin guard to every route, which an MCP client cannot
    # satisfy: it is not a browser and sends no Origin. Its own authentication
    # is the bearer token each tool checks. Starlette matches mounts in order,
    # so this must come first or /hub would swallow the path.
    from metaseed_hub.mcp import create_mcp_server

    mcp_server = create_mcp_server()
    # Kept on app.state so the lifespan above can start its session manager.
    app.state.mcp_server = mcp_server
    app.mount(MCP_PATH, mcp_server.streamable_http_app())
    app.add_middleware(_AcceptMcpWithoutTrailingSlash)

    app.mount("/hub", hub_app)

    # Redirect root to hub
    from fastapi.responses import PlainTextResponse, RedirectResponse

    @app.get("/robots.txt", include_in_schema=False)
    async def robots() -> PlainTextResponse:
        # The app is behind login, so crawlers only ever reach the landing page.
        # Keep the analytics tracker and API out of any index. The tracker is
        # served at /matomo/ (nginx proxies matomo.php and matomo.js there), not
        # under /hub/, so the disallow has to name the path that actually exists.
        return PlainTextResponse("User-agent: *\nDisallow: /api/\nDisallow: /matomo/\nAllow: /\n")

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
            async with db.session_factory() as session:
                await get_dataset_for_user(project_id, session, user)
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
