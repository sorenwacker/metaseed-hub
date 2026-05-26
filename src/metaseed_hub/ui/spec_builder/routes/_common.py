"""Common imports and helpers for spec builder routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.database import get_session
from metaseed_hub.ui.spec_builder.access import DraftContext, get_draft_context

# Type aliases for dependency injection
SessionDep = Annotated[AsyncSession, Depends(get_session)]
DraftContextDep = Annotated[DraftContext, Depends(get_draft_context)]


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
