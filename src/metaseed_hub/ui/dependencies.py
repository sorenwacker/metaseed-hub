"""Shared FastAPI dependencies for Hub UI routes."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, verify_token
from metaseed_hub.database import get_session
from metaseed_hub.models import Project, Tenant, Workspace

ACCESS_TOKEN_COOKIE = "metaseed_access_token"
CSRF_TOKEN_COOKIE = "metaseed_csrf_token"


class AuthRequiredError(Exception):
    """Raised when authentication is required but user is not authenticated."""

    def __init__(self, is_htmx: bool = False) -> None:
        self.is_htmx = is_htmx
        super().__init__("Authentication required")


def get_or_create_csrf_token(request: Request) -> str:
    """Get existing CSRF token from cookie or create a new one."""
    token = request.cookies.get(CSRF_TOKEN_COOKIE)
    if token and len(token) == 43:  # Base64 encoded 32 bytes
        return token
    return secrets.token_urlsafe(32)


async def get_current_user_from_cookie(request: Request) -> TokenUser | None:
    """Extract and verify user from access token cookie.

    Returns None if no token or invalid token.
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        return await verify_token(token)
    except Exception:
        return None


async def require_user(request: Request) -> TokenUser:
    """Require authenticated user, redirect to login if not authenticated.

    Use as a FastAPI dependency to protect routes.
    Raises AuthRequiredError which is handled by the app exception handler.
    """
    user = await get_current_user_from_cookie(request)
    if not user:
        is_htmx = request.headers.get("HX-Request") == "true"
        raise AuthRequiredError(is_htmx=is_htmx)
    return user


def handle_auth_required_error(request: Request, exc: Exception) -> Response:
    """Handle AuthRequiredError by redirecting to login.

    For HTMX requests, returns 401 with HX-Redirect header.
    For regular requests, returns 302 redirect.
    """
    if isinstance(exc, AuthRequiredError) and exc.is_htmx:
        return Response(
            content="Session expired",
            status_code=401,
            headers={"HX-Redirect": "/hub/auth/login"},
        )
    return RedirectResponse(url="/hub/auth/login", status_code=302)


def unauthorized_response() -> HTMLResponse:
    """Return a user-friendly unauthorized response for HTMX requests."""
    return HTMLResponse(
        """<div class="error-message" style="padding: 1rem;">
            <strong>Session expired.</strong>
            Please <a href="/hub/auth/login" target="_top">log in again</a> to continue.
        </div>""",
        status_code=401,
    )


async def get_project_by_id(
    project_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Project:
    """Get project by ID or raise 404.

    Use as a FastAPI dependency to load and validate project access.
    Note: This function does NOT verify ownership. Use get_project_for_user()
    for endpoints that require ownership verification.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def get_tenant_for_user(session: AsyncSession, user: TokenUser) -> Tenant | None:
    """Get or create tenant for user based on keycloak_id.

    Args:
        session: Database session.
        user: Authenticated user.

    Returns:
        Tenant for the user, or None if not found.
    """
    slug = user.keycloak_id[:8]
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def verify_workspace_access(
    workspace_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Workspace:
    """Verify user has access to workspace and return it.

    A user has access to a workspace if their tenant owns the workspace.

    Args:
        workspace_id: ID of the workspace to verify.
        session: Database session.
        user: Authenticated user.

    Returns:
        Workspace if user has access.

    Raises:
        HTTPException: 404 if workspace not found, 403 if access denied.
    """
    result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get user's tenant
    tenant = await get_tenant_for_user(session, user)
    if not tenant:
        raise HTTPException(status_code=403, detail="Access denied")

    # Verify workspace belongs to user's tenant
    if workspace.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return workspace


async def get_project_for_user(
    project_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Project:
    """Get project if user has access through workspace membership.

    A user has access to a project if their tenant owns the workspace
    that contains the project.

    Args:
        project_id: ID of the project to retrieve.
        session: Database session.
        user: Authenticated user.

    Returns:
        Project if user has access.

    Raises:
        HTTPException: 404 if project not found, 403 if access denied.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Verify user has access to the workspace
    await verify_workspace_access(project.workspace_id, session, user)

    return project


# Type aliases for cleaner route signatures
CurrentUser = Annotated[TokenUser, Depends(require_user)]
OptionalUser = Annotated[TokenUser | None, Depends(get_current_user_from_cookie)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
