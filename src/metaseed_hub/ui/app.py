"""Hub UI application that extends metaseed's HTMX interface.

Adds authentication, project management, and collaboration features
on top of the base metaseed entity editing UI.
"""

import logging
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.database import get_session
from metaseed_hub.models import Tenant, Workspace
from metaseed_hub.ui.dependencies import (
    AuthRequiredError,
    OptionalUser,
    get_or_create_csrf_token,
    handle_auth_required_error,
)
from metaseed_hub.ui.explore_routes import create_explore_router
from metaseed_hub.ui.helpers import (
    CSRF_TOKEN_COOKIE,
    escape_pattern_hyphen,
    humanize_field_name,
)
from metaseed_hub.ui.routes import (
    auth_router,
    entity_router,
    init_entity_templates,
    init_project_templates,
    init_workspace_templates,
    project_router,
    table_router,
    workspace_router,
)
from metaseed_hub.ui.spec_builder_routes import create_spec_builder_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("metaseed_hub")

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


@lru_cache(maxsize=1)
def get_version_info() -> dict[str, str]:
    """Get version information from git and package.

    Returns:
        Dictionary with version, commit hash and branch name.
    """
    info = {"version": "dev", "commit": "unknown", "branch": "unknown", "short_commit": "unknown"}

    # Get package version
    try:
        from metaseed_hub._version import __version__

        info["version"] = __version__
    except ImportError:
        pass

    try:
        # Get short commit hash
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=UI_DIR,
            timeout=5,
        )
        if result.returncode == 0:
            info["short_commit"] = result.stdout.strip()

        # Get full commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=UI_DIR,
            timeout=5,
        )
        if result.returncode == 0:
            info["commit"] = result.stdout.strip()

        # Get branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=UI_DIR,
            timeout=5,
        )
        if result.returncode == 0:
            info["branch"] = result.stdout.strip()

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return info


def create_hub_app() -> FastAPI:
    """Create the Hub FastAPI application with extended UI.

    Returns:
        FastAPI application with hub routes and mounted metaseed UI.
    """
    app = FastAPI(title="Metaseed Hub")

    # Register exception handler for auth redirects
    app.add_exception_handler(AuthRequiredError, handle_auth_required_error)

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Register template filters
    templates.env.filters["escape_pattern"] = escape_pattern_hyphen
    templates.env.filters["humanize"] = humanize_field_name

    # Initialize templates for route modules
    init_workspace_templates(templates)
    init_project_templates(templates)
    init_entity_templates(templates)

    # Mount hub static files
    app.mount("/hub-static", StaticFiles(directory=str(STATIC_DIR)), name="hub-static")

    # Include route modules
    app.include_router(auth_router)
    app.include_router(workspace_router)
    app.include_router(project_router)
    app.include_router(entity_router)
    app.include_router(table_router)

    # Add spec builder routes
    spec_builder_router = create_spec_builder_router(templates)
    app.include_router(spec_builder_router)

    # Add explore routes
    explore_router = create_explore_router(templates)
    app.include_router(explore_router)

    def render_template(
        request: Request,
        name: str,
        context: dict[str, Any],
        status_code: int = 200,
    ) -> Response:
        """Render template with CSRF token and version info included."""
        csrf_token = get_or_create_csrf_token(request)
        context["csrf_token"] = csrf_token
        context["request"] = request
        context["version_info"] = get_version_info()

        response = templates.TemplateResponse(
            request=request,
            name=name,
            context=context,
            status_code=status_code,
        )

        # Set CSRF cookie if not already set
        if not request.cookies.get(CSRF_TOKEN_COOKIE):
            response.set_cookie(
                key=CSRF_TOKEN_COOKIE,
                value=csrf_token,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="lax",
                max_age=3600 * 24,  # 24 hours
            )

        return response

    async def get_or_create_tenant(session: AsyncSession, user: OptionalUser) -> Tenant:
        """Get or create tenant for user based on keycloak_id."""
        if not user:
            raise ValueError("User required")
        # Use keycloak_id as tenant slug for single-user tenants
        slug = user.keycloak_id[:8]
        result = await session.execute(select(Tenant).where(Tenant.slug == slug))
        tenant = result.scalar_one_or_none()
        if not tenant:
            tenant = Tenant(name=user.name or user.email, slug=slug)
            session.add(tenant)
            await session.commit()
            await session.refresh(tenant)
        return tenant

    @app.get("/", response_class=Response)
    async def home(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        user: OptionalUser,
    ) -> Response:
        """Home page - show workspaces and projects."""
        if not user:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"request": request},
            )

        # Get or create tenant for user
        tenant = await get_or_create_tenant(session, user)

        # Get user's workspaces
        result = await session.execute(select(Workspace).where(Workspace.tenant_id == tenant.id))
        workspaces = list(result.scalars().all())

        return render_template(
            request=request,
            name="home.html",
            context={
                "user": user,
                "workspaces": workspaces,
                "nav_active": "home",
            },
        )

    @app.get("/privacy", response_class=Response)
    async def privacy_policy(request: Request, user: OptionalUser) -> Response:
        """Privacy policy page."""
        return templates.TemplateResponse(
            request=request,
            name="privacy.html",
            context={"request": request, "user": user},
        )

    @app.get("/aup", response_class=Response)
    async def acceptable_use_policy(request: Request, user: OptionalUser) -> Response:
        """Acceptable use policy page."""
        return templates.TemplateResponse(
            request=request,
            name="aup.html",
            context={"request": request, "user": user},
        )

    return app
