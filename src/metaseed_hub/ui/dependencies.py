"""Shared FastAPI dependencies for Hub UI routes."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, verify_token
from metaseed_hub.database import get_session
from metaseed_hub.models import Project

ACCESS_TOKEN_COOKIE = "metaseed_access_token"
CSRF_TOKEN_COOKIE = "metaseed_csrf_token"


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
    """Require authenticated user, raise 401 if not authenticated.

    Use as a FastAPI dependency to protect routes.
    """
    user = await get_current_user_from_cookie(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"HX-Redirect": "/hub/auth/login"},
        )
    return user


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
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


# Type aliases for cleaner route signatures
CurrentUser = Annotated[TokenUser, Depends(require_user)]
OptionalUser = Annotated[TokenUser | None, Depends(get_current_user_from_cookie)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
