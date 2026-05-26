"""Common imports and helpers for spec builder routes."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, NewType

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.database import get_session
from metaseed_hub.ui.spec_builder.access import (
    DraftContext,
    LoginRequiredRedirectError,
    get_draft_context,
)
from metaseed_hub.ui.spec_builder.access import (
    get_user_context as _get_user_context,
)

__all__ = [
    "SessionDep",
    "DraftContextDep",
    "UserContextDep",
    "UserId",
    "TenantId",
    "RenderFunc",
    "render_with_context",
    "create_render_helper",
]

# Type aliases for clarity
UserId = NewType("UserId", str)
TenantId = NewType("TenantId", str)

# Type aliases for dependency injection
SessionDep = Annotated[AsyncSession, Depends(get_session)]
DraftContextDep = Annotated[DraftContext, Depends(get_draft_context)]


async def _require_user_context(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> tuple[UserId, TenantId]:
    """Dependency that requires authenticated user, redirects if not.

    Returns:
        Tuple of (user_id, tenant_id) where user_id is the database User.id.

    Raises:
        HTTPException: 302 redirect if user is not authenticated.
    """
    from fastapi import HTTPException

    try:
        user_id, tenant_id = await _get_user_context(
            request, session, redirect_on_unauthorized=True
        )
        return UserId(user_id), TenantId(tenant_id)
    except LoginRequiredRedirectError:
        raise HTTPException(
            status_code=302,
            headers={"Location": "/hub/auth/login"},
        )


# Dependency that provides authenticated user context
UserContextDep = Annotated[tuple[UserId, TenantId], Depends(_require_user_context)]


async def render_with_context(
    templates: Jinja2Templates,
    request: Request,
    template: str,
    context: dict[str, Any],
) -> Response:
    """Render template with version info, nav_active, and user included."""
    from metaseed_hub.ui.app import get_version_info
    from metaseed_hub.ui.dependencies import get_current_user_from_cookie

    context["version_info"] = get_version_info()
    context["nav_active"] = "spec-builder"
    if "user" not in context:
        context["user"] = await get_current_user_from_cookie(request)
    return templates.TemplateResponse(request, template, context)


RenderFunc = Callable[[Request, str, dict[str, Any]], Coroutine[Any, Any, Response]]


def create_render_helper(templates: Jinja2Templates) -> RenderFunc:
    """Create a render helper bound to a templates instance.

    Usage:
        render = create_render_helper(templates)
        return await render(request, "template.html", {"key": "value"})
    """

    async def render(request: Request, template: str, context: dict[str, Any]) -> Response:
        return await render_with_context(templates, request, template, context)

    return render
