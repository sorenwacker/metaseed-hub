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
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.database import get_session
from metaseed_hub.models import (
    Dataset,
    DatasetMember,
    SpecDraft,
    SpecDraftMember,
    Tenant,
    User,
    Workspace,
)
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
    dataset_router,
    entity_router,
    init_dataset_templates,
    init_entity_templates,
    init_workspace_templates,
    ontology_router,
    table_router,
    workspace_router,
)
from metaseed_hub.ui.routes.auth import (
    ACCESS_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    REFRESH_TOKEN_MAX_AGE,
    refresh_access_token,
)
from metaseed_hub.ui.spec_builder import create_spec_builder_router

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
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    from metaseed_hub.auth import verify_token
    from metaseed_hub.config import get_settings

    app = FastAPI(title="Metaseed Hub")

    # Token refresh middleware - auto-refresh expired tokens
    class TokenRefreshMiddleware(BaseHTTPMiddleware):
        """Middleware that refreshes expired access tokens using refresh tokens."""

        async def dispatch(self, request: StarletteRequest, call_next: Any) -> StarletteResponse:
            """Check and refresh tokens before processing request."""
            access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)
            refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
            new_tokens: dict[str, Any] | None = None

            # If we have an access token, verify it
            if access_token:
                try:
                    await verify_token(access_token)
                except Exception:
                    # Token invalid/expired, try to refresh
                    if refresh_token:
                        new_tokens = await refresh_access_token(refresh_token)
                        if new_tokens:
                            # Store refreshed token in request state for downstream
                            request.state.refreshed_access_token = new_tokens["access_token"]
            elif refresh_token:
                # No access token but have refresh token - try to refresh
                new_tokens = await refresh_access_token(refresh_token)
                if new_tokens:
                    request.state.refreshed_access_token = new_tokens["access_token"]

            # Process request
            response: StarletteResponse = await call_next(request)

            # If we got new tokens, set them on the response
            if new_tokens:
                settings = get_settings()
                response.set_cookie(
                    key=ACCESS_TOKEN_COOKIE,
                    value=new_tokens["access_token"],
                    httponly=True,
                    secure=not settings.debug,
                    samesite="lax",
                    max_age=int(new_tokens.get("expires_in", 3600)),
                    path="/",
                )
                if new_tokens.get("refresh_token"):
                    response.set_cookie(
                        key=REFRESH_TOKEN_COOKIE,
                        value=str(new_tokens["refresh_token"]),
                        httponly=True,
                        secure=not settings.debug,
                        samesite="lax",
                        max_age=REFRESH_TOKEN_MAX_AGE,
                        path="/",
                    )

            return response

    app.add_middleware(TokenRefreshMiddleware)

    # Register exception handler for auth redirects
    app.add_exception_handler(AuthRequiredError, handle_auth_required_error)

    # Get metaseed's template directory for reusing Explorer templates
    import metaseed.ui

    metaseed_templates_dir = Path(metaseed.ui.__file__).parent / "templates"

    # Create Jinja2 with multiple template directories (hub first, then metaseed)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(TEMPLATES_DIR)),
            FileSystemLoader(str(metaseed_templates_dir)),
        ]
    )

    # Register template filters
    templates.env.filters["escape_pattern"] = escape_pattern_hyphen
    templates.env.filters["humanize"] = humanize_field_name

    # Initialize templates for route modules
    init_workspace_templates(templates)
    init_dataset_templates(templates)
    init_entity_templates(templates)

    # Mount hub static files
    app.mount("/hub-static", StaticFiles(directory=str(STATIC_DIR)), name="hub-static")

    # Mount metaseed's static files for Explorer template
    metaseed_static_dir = Path(metaseed.ui.__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(metaseed_static_dir)), name="metaseed-static")

    # Include route modules
    app.include_router(auth_router)
    app.include_router(workspace_router)
    app.include_router(dataset_router)
    app.include_router(entity_router)
    app.include_router(table_router)
    app.include_router(ontology_router)

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
        """Home page - show datasets and specs."""
        if not user:
            return render_template(
                request=request,
                name="login.html",
                context={},
            )

        # Get or create tenant for user
        tenant = await get_or_create_tenant(session, user)

        # Get the database User record for shared spec queries
        db_user_result = await session.execute(
            select(User).where(User.keycloak_id == user.keycloak_id)
        )
        db_user = db_user_result.scalar_one_or_none()

        # Get user's workspaces
        result = await session.execute(select(Workspace).where(Workspace.tenant_id == tenant.id))
        workspaces = list(result.scalars().all())
        workspace_ids = [w.id for w in workspaces]

        # Get owned datasets from user's workspaces
        owned_datasets: list[Dataset] = []
        if workspace_ids:
            ds_result = await session.execute(
                select(Dataset)
                .where(Dataset.workspace_id.in_(workspace_ids), Dataset.deleted_at.is_(None))
                .order_by(Dataset.updated_at.desc())
            )
            owned_datasets = list(ds_result.scalars().all())

        # Get datasets shared with this user via DatasetMember
        shared_datasets: list[Dataset] = []
        if db_user:
            shared_ds_result = await session.execute(
                select(Dataset)
                .join(DatasetMember, DatasetMember.dataset_id == Dataset.id)
                .where(DatasetMember.user_id == db_user.id, Dataset.deleted_at.is_(None))
                .order_by(Dataset.updated_at.desc())
            )
            shared_datasets = list(shared_ds_result.scalars().all())

        # Combine and deduplicate datasets
        seen_ds_ids: set[str] = set()
        datasets: list[Dataset] = []
        for ds in owned_datasets + shared_datasets:
            if ds.id not in seen_ds_ids:
                seen_ds_ids.add(ds.id)
                datasets.append(ds)

        # Get owned spec drafts from user's workspaces
        owned_specs: list[SpecDraft] = []
        if workspace_ids:
            spec_result = await session.execute(
                select(SpecDraft)
                .where(SpecDraft.workspace_id.in_(workspace_ids))
                .order_by(SpecDraft.updated_at.desc())
            )
            owned_specs = list(spec_result.scalars().all())

        # Get specs shared with this user via SpecDraftMember
        shared_specs: list[SpecDraft] = []
        if db_user:
            shared_result = await session.execute(
                select(SpecDraft)
                .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
                .where(SpecDraftMember.user_id == db_user.id)
                .order_by(SpecDraft.updated_at.desc())
            )
            shared_specs = list(shared_result.scalars().all())

        # Combine and deduplicate specs
        seen_ids: set[str] = set()
        specs: list[SpecDraft] = []
        for spec in owned_specs + shared_specs:
            if spec.id not in seen_ids:
                seen_ids.add(spec.id)
                specs.append(spec)

        return render_template(
            request=request,
            name="home.html",
            context={
                "user": user,
                "workspaces": workspaces,
                "datasets": datasets,
                "specs": specs,
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
