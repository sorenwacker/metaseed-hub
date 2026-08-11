"""The sharing routes, one set for every shared thing.

There were two nearly identical routers — one for datasets, one for
specification drafts — and none at all for published specifications, which is
why handing one to a colleague meant editing the database directly. The rules
live in :mod:`metaseed_hub.sharing`; these routes are the thin part: resolve
who is asking, call the rule, render the list again.

Every endpoint answers with the members list as HTML, so one panel template
serves all three kinds and htmx swaps it in place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from metaseed_hub.auth import TokenUser
from metaseed_hub.sharing import (
    Role,
    SharedResource,
    SharingError,
    add_member,
    may_see_members,
    members_of,
    remove_member,
    resource_for,
    role_of,
    set_role,
)
from metaseed_hub.ui.dependencies import DbSession, require_user
from metaseed_hub.ui.helpers.csrf import validate_csrf_token
from metaseed_hub.ui.render import render_template

router = APIRouter(prefix="/sharing", tags=["sharing"])

CurrentUser = Annotated[TokenUser, Depends(require_user)]


def _check_csrf(request: Request, form_token: str | None) -> None:
    """Refuse a change that carries no proof it came from our own page.

    Sharing is a state change driven by a form post; without this, another site
    could add itself to a colleague's dataset by pointing a form at this URL.
    The routes these replaced each carried this check, and dropping it in the
    move would have been a silent regression.
    """
    if not validate_csrf_token(request, form_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def _resource_or_404(kind: str) -> SharedResource:
    try:
        return resource_for(kind)
    except KeyError:
        # The kind comes from the URL; an unknown one is a wrong address, not a
        # server fault.
        raise HTTPException(status_code=404, detail="No such thing to share") from None


async def _db_user_id(session: DbSession, user: TokenUser) -> str:
    from metaseed_hub.ui.dependencies import ensure_tenant_and_user

    _, db_user = await ensure_tenant_and_user(session, user)
    return str(db_user.id)


async def _viewer(session: DbSession, user: TokenUser) -> tuple[str, str]:
    """The asking person's user and account ids."""
    from metaseed_hub.ui.dependencies import ensure_tenant_and_user

    tenant, db_user = await ensure_tenant_and_user(session, user)
    return str(db_user.id), str(tenant.id)


async def _seen_or_404(
    session: DbSession, resource: SharedResource, resource_id: str, user: TokenUser
) -> str:
    """The viewer's id, once they are allowed to see this resource's members.

    404 rather than 403: a stranger learns nothing about what exists.
    """
    viewer_id, tenant_id = await _viewer(session, user)
    if not await may_see_members(
        session, resource, resource_id, user_id=viewer_id, tenant_id=tenant_id
    ):
        raise HTTPException(status_code=404, detail="Not found")
    return viewer_id


async def _panel(
    request: Request,
    session: DbSession,
    resource: SharedResource,
    resource_id: str,
    viewer_id: str,
    *,
    error: str | None = None,
) -> HTMLResponse:
    """The members list as the asking person may see and act on it."""
    members = await members_of(session, resource, resource_id)
    return render_template(  # type: ignore[return-value]
        request,
        "partials/members_panel.html",
        {
            "members": members,
            "kind": resource.kind,
            "resource_id": resource_id,
            "viewer_id": viewer_id,
            "viewer_is_owner": await role_of(session, resource, resource_id, viewer_id)
            is Role.OWNER,
            "roles": list(Role),
            "error": error,
        },
    )


@router.get("/{kind}/{resource_id}/members", response_class=HTMLResponse)
async def list_members(
    request: Request, kind: str, resource_id: str, session: DbSession, user: CurrentUser
) -> HTMLResponse:
    """Who has access, and the controls for changing that."""
    resource = _resource_or_404(kind)
    viewer_id = await _seen_or_404(session, resource, resource_id, user)
    return await _panel(request, session, resource, resource_id, viewer_id)


@router.post("/{kind}/{resource_id}/members", response_class=HTMLResponse)
async def add(
    request: Request,
    kind: str,
    resource_id: str,
    session: DbSession,
    user: CurrentUser,
    email: str = Form(...),
    role: str = Form(Role.VIEWER.value),
    csrf_token: str | None = Form(None),
) -> HTMLResponse:
    """Give someone access by the address on their profile."""
    _check_csrf(request, csrf_token)
    resource = _resource_or_404(kind)
    viewer_id = await _seen_or_404(session, resource, resource_id, user)
    error = None
    try:
        await add_member(
            session,
            resource,
            resource_id,
            actor_id=viewer_id,
            email=email,
            role=_role_or_refuse(role),
        )
    except SharingError as exc:
        error = str(exc)
    return await _panel(request, session, resource, resource_id, viewer_id, error=error)


@router.patch("/{kind}/{resource_id}/members/{member_id}", response_class=HTMLResponse)
async def change_role(
    request: Request,
    kind: str,
    resource_id: str,
    member_id: str,
    session: DbSession,
    user: CurrentUser,
    role: str = Form(...),
    csrf_token: str | None = Form(None),
) -> HTMLResponse:
    """Change what one member may do."""
    _check_csrf(request, csrf_token)
    resource = _resource_or_404(kind)
    viewer_id = await _seen_or_404(session, resource, resource_id, user)
    error = None
    try:
        await set_role(
            session,
            resource,
            resource_id,
            actor_id=viewer_id,
            user_id=member_id,
            role=_role_or_refuse(role),
        )
    except SharingError as exc:
        error = str(exc)
    return await _panel(request, session, resource, resource_id, viewer_id, error=error)


@router.delete("/{kind}/{resource_id}/members/{member_id}", response_class=HTMLResponse)
async def remove(
    request: Request,
    kind: str,
    resource_id: str,
    member_id: str,
    session: DbSession,
    user: CurrentUser,
    csrf_token: str | None = Form(None),
) -> HTMLResponse:
    """Take away access — or leave, when removing yourself."""
    _check_csrf(request, csrf_token)
    resource = _resource_or_404(kind)
    viewer_id = await _seen_or_404(session, resource, resource_id, user)
    error = None
    try:
        await remove_member(session, resource, resource_id, actor_id=viewer_id, user_id=member_id)
    except SharingError as exc:
        error = str(exc)
    return await _panel(request, session, resource, resource_id, viewer_id, error=error)


def _role_or_refuse(role: str) -> Role:
    """``role`` as a :class:`Role`, refusing anything else.

    The value arrives from a form, so it is whatever the browser sent.
    """
    try:
        return Role(role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"No such role: {role}") from None
