"""Hub UI application that extends metaseed's HTMX interface.

Adds authentication, project management, and collaboration features
on top of the base metaseed entity editing UI.
"""

import logging
import sys
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
)
from metaseed_hub.ui.dependencies import (
    AuthRequiredError,
    OptionalUser,
    ensure_tenant_and_user,
    handle_auth_required_error,
)
from metaseed_hub.ui.explore_routes import create_explore_router
from metaseed_hub.ui.helpers import (
    escape_pattern_hyphen,
    humanize_field_name,
)
from metaseed_hub.ui.metaseed_ui import METASEED_STATIC_DIR, METASEED_TEMPLATES_DIR
from metaseed_hub.ui.render import get_repo_stars, render_template
from metaseed_hub.ui.routes import (
    admin_router,
    auth_router,
    dataset_router,
    entity_router,
    init_admin_templates,
    init_dataset_templates,
    init_entity_templates,
    is_admin,
    ontology_router,
    table_router,
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

    # Apply the Origin-based CSRF guard to every cookie-authenticated hub route,
    # not just the spec builder. The /api surface is token-authenticated and
    # mounted separately, so it is unaffected.
    from metaseed_hub.ui.security import require_same_origin

    app = FastAPI(title="Metaseed Hub", dependencies=[Depends(require_same_origin)])

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

    # Outermost of the two, so it sees anything the request raises. It records
    # and re-raises, leaving the 500 response and the log line unchanged.
    from metaseed_hub.errors import ErrorRecordingMiddleware

    app.add_middleware(ErrorRecordingMiddleware)

    # Register exception handler for auth redirects
    app.add_exception_handler(AuthRequiredError, handle_auth_required_error)

    # A spec-builder save that would overwrite someone else's edit is refused
    # rather than reported as a success. Handled centrally so every editing
    # route reports it the same way instead of each remembering to catch it.
    from metaseed_hub.ui.spec_builder.access import (
        DraftConflictError,
        handle_draft_conflict,
    )

    app.add_exception_handler(DraftConflictError, handle_draft_conflict)

    # A stored profile version that predates the MAJOR.MINOR rule cannot be
    # deserialized, and the page the author would fix it from is the page that
    # fails. Handled centrally so every reader of stored spec data reports the
    # same fixable problem instead of a 500.
    from metaseed_hub.ui.spec_builder.versioning import (
        SpecVersionError,
        handle_spec_version_error,
    )

    app.add_exception_handler(SpecVersionError, handle_spec_version_error)

    # Create Jinja2 with multiple template directories (hub first, then
    # metaseed's Explorer templates)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(TEMPLATES_DIR)),
            FileSystemLoader(str(METASEED_TEMPLATES_DIR)),
        ]
    )

    # Register template filters
    templates.env.filters["escape_pattern"] = escape_pattern_hyphen
    templates.env.filters["humanize"] = humanize_field_name

    # Initialize templates for route modules
    init_dataset_templates(templates)
    init_entity_templates(templates)
    init_admin_templates(templates)

    # Register is_admin as template global for conditional nav rendering
    templates.env.globals["is_admin"] = is_admin

    # Expose the cached GitHub star count to the footer on every page
    templates.env.globals["get_repo_stars"] = get_repo_stars

    # Mount hub static files
    app.mount("/hub-static", StaticFiles(directory=str(STATIC_DIR)), name="hub-static")

    # Mount metaseed's static files for Explorer template
    app.mount("/static", StaticFiles(directory=str(METASEED_STATIC_DIR)), name="metaseed-static")

    # Include route modules
    app.include_router(auth_router)
    app.include_router(dataset_router)
    app.include_router(entity_router)
    app.include_router(table_router)
    app.include_router(ontology_router)
    app.include_router(admin_router)

    # Add spec builder routes
    spec_builder_router = create_spec_builder_router(templates)
    app.include_router(spec_builder_router)

    # Add explore routes
    explore_router = create_explore_router(templates)
    app.include_router(explore_router)

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

        # Get or create the tenant and the database User record in one place,
        # via the canonical helper, so onboarding stays consistent across routes.
        tenant, db_user = await ensure_tenant_and_user(session, user)

        # Get owned datasets from tenant
        ds_result = await session.execute(
            select(Dataset)
            .where(Dataset.tenant_id == tenant.id, Dataset.deleted_at.is_(None))
            .order_by(Dataset.updated_at.desc())
        )
        owned_datasets = list(ds_result.scalars().all())

        # Get datasets shared with this user via DatasetMember
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

        # Get owned spec drafts from tenant
        spec_result = await session.execute(
            select(SpecDraft)
            .where(SpecDraft.tenant_id == tenant.id)
            .order_by(SpecDraft.updated_at.desc())
        )
        owned_specs = list(spec_result.scalars().all())

        # Get specs shared with this user via SpecDraftMember
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
                "tenant": tenant,
                "datasets": datasets,
                "specs": specs,
                "nav_active": "home",
            },
        )

    @app.get("/home", response_class=Response)
    async def overview_home(request: Request, user: OptionalUser) -> Response:
        """What the hub is for.

        Its own page rather than a banner on the dataset list: that list is what
        a returning user comes for, and an explanation above it pushes their own
        work down the page every visit.
        """
        if not user:
            from fastapi.responses import RedirectResponse

            return RedirectResponse("/hub/", status_code=302)
        return render_template(
            request=request,
            name="overview_home.html",
            context={"user": user, "nav_active": "overview"},
        )

    @app.get("/privacy", response_class=Response)
    async def privacy_policy(request: Request, user: OptionalUser) -> Response:
        """Privacy policy page."""
        return render_template(
            request=request,
            name="privacy.html",
            context={"user": user},
        )

    @app.get("/aup", response_class=Response)
    async def acceptable_use_policy(request: Request, user: OptionalUser) -> Response:
        """Acceptable use policy page."""
        return render_template(
            request=request,
            name="aup.html",
            context={"user": user},
        )

    return app
