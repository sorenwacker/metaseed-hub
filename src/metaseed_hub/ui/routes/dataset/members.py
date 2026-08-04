"""Dataset membership/sharing routes."""

import logging
from typing import Annotated

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metaseed_hub.models import (
    DatasetMember,
    DatasetRole,
    User,
)
from metaseed_hub.ui.dependencies import (
    CurrentUser,
    DbSession,
    require_dataset_owner,
)
from metaseed_hub.ui.helpers import normalize_email
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

from ._router import router

logger = logging.getLogger("metaseed_hub")


async def _get_members_html(
    request: Request,
    dataset_id: str,
    session: DbSession,
) -> Response:
    """Render the member list partial."""
    result = await session.execute(
        select(DatasetMember)
        .where(DatasetMember.dataset_id == dataset_id)
        .options(selectinload(DatasetMember.user))
    )
    members = list(result.scalars().all())

    return render_template(
        request=request,
        name="partials/dataset_members.html",
        context={
            "members": members,
            "dataset_id": dataset_id,
        },
    )


@router.post("/{dataset_id}/members", response_class=HTMLResponse)
async def add_dataset_member(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
    email: Annotated[str, Form()],
) -> Response:
    """Add a member to a dataset by email."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Only an owner may manage membership (not an ordinary VIEWER/member).
    await require_dataset_owner(dataset_id, session, user)

    # Deliberately not tenant-scoped: every account has its own tenant, so an
    # invitee is always outside the dataset's. uq_users_email makes the address
    # identify exactly one account, so this cannot match a stranger.
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    target_user = result.scalar_one_or_none()

    if not target_user:
        response = await _get_members_html(request, dataset_id, session)
        msg = "No account uses that email. Ask them to log in once, then share again."
        response.headers["HX-Trigger"] = f'{{"showToast": {{"message": "{msg}", "type": "error"}}}}'
        return response

    # Check if already a member
    existing = await session.execute(
        select(DatasetMember).where(
            DatasetMember.dataset_id == dataset_id,
            DatasetMember.user_id == target_user.id,
        )
    )
    if existing.scalar_one_or_none():
        return await _get_members_html(request, dataset_id, session)

    # Add member with viewer role by default
    member = DatasetMember(
        dataset_id=dataset_id,
        user_id=target_user.id,
        role=DatasetRole.VIEWER,
    )
    session.add(member)
    await session.commit()

    # Refresh to ensure we can load relationships
    await session.refresh(member)

    return await _get_members_html(request, dataset_id, session)


@router.patch("/{dataset_id}/members/{user_id}", response_class=HTMLResponse)
async def update_dataset_member_role(
    request: Request,
    dataset_id: str,
    user_id: str,
    session: DbSession,
    user: CurrentUser,
    role: Annotated[str, Form()],
) -> Response:
    """Update a member's role in a dataset."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Only an owner may change member roles.
    await require_dataset_owner(dataset_id, session, user)

    try:
        new_role = DatasetRole(role)
    except ValueError:
        return HTMLResponse("<div class='error'>Invalid role</div>", status_code=400)

    # Find membership
    result = await session.execute(
        select(DatasetMember).where(
            DatasetMember.dataset_id == dataset_id,
            DatasetMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()

    if member:
        member.role = new_role
        await session.commit()

    return await _get_members_html(request, dataset_id, session)


@router.delete("/{dataset_id}/members/{user_id}", response_class=HTMLResponse)
async def remove_dataset_member(
    request: Request,
    dataset_id: str,
    user_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Remove a member from a dataset."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Only an owner may remove members.
    await require_dataset_owner(dataset_id, session, user)

    # Find and delete membership
    result = await session.execute(
        select(DatasetMember).where(
            DatasetMember.dataset_id == dataset_id,
            DatasetMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()

    if member:
        await session.delete(member)
        await session.commit()

    return await _get_members_html(request, dataset_id, session)


# =============================================================================
# Comment Routes (Threaded, with reactions)
# =============================================================================
