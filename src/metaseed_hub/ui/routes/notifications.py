"""Notifications: the bell's count and the list it opens."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from metaseed_hub.notifications import open_list, unread_count
from metaseed_hub.ui import dependencies
from metaseed_hub.ui.dependencies import CurrentUser, DbSession, ensure_tenant_and_user
from metaseed_hub.ui.render import render_template

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_class=HTMLResponse)
async def notifications_page(request: Request, session: DbSession, user: CurrentUser) -> Response:
    """The person's notifications; showing them marks them read."""
    _tenant, db_user = await ensure_tenant_and_user(session, user)
    return render_template(
        request,
        "notifications.html",
        {
            "user": user,
            "nav_active": "notifications",
            "entries": await open_list(session, db_user.id),
        },
    )


@router.get("/badge", response_class=HTMLResponse)
async def notifications_badge(request: Request, session: DbSession) -> Response:
    """The unread count as the bell shows it: the number, or nothing for none.

    Asked once a minute by every open page, so a session that has ended is
    answered with nothing. Requiring sign-in here would send a page that was
    merely left open to the sign-in screen, taking what was typed with it.
    """
    user = await dependencies.get_current_user_from_cookie(request)
    if not user:
        return HTMLResponse("")
    _tenant, db_user = await ensure_tenant_and_user(session, user)
    count = await unread_count(session, db_user.id)
    return HTMLResponse(str(count) if count else "")
