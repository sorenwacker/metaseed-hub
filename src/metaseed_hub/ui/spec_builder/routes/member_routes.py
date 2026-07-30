"""Member/sharing routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from metaseed_hub.models import SpecDraft, SpecDraftMember, SpecDraftRole, User
from metaseed_hub.ui.spec_builder.access import require_draft_owner

from ._common import SessionDep, UserContextDep

__all__ = ["register_member_routes"]


def register_member_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register member/sharing routes."""

    async def _get_spec_members_html(
        request: Request,
        draft_id: str,
        session: AsyncSession,
        user_id: str,
    ) -> HTMLResponse:
        """Render the member list partial for a spec draft.

        Args:
            request: FastAPI request
            draft_id: Draft ID
            session: Database session
            user_id: Database User.id (not keycloak_id)
        """
        # Get draft to find owner
        draft_result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
        draft = draft_result.scalar_one_or_none()

        draft_owner = None
        is_current_user_owner = False
        if draft:
            # Load owner - draft.user_id is FK to users.id
            owner_result = await session.execute(select(User).where(User.id == draft.user_id))
            draft_owner = owner_result.scalar_one_or_none()
            is_current_user_owner = draft.user_id == user_id

        # Get members
        result = await session.execute(
            select(SpecDraftMember)
            .where(SpecDraftMember.spec_draft_id == draft_id)
            .options(selectinload(SpecDraftMember.user))
        )
        members = list(result.scalars().all())

        return templates.TemplateResponse(
            request,
            "partials/spec_draft_members.html",
            {
                "members": members,
                "draft_id": draft_id,
                "draft_owner": draft_owner,
                "current_user_id": user_id,
                "is_current_user_owner": is_current_user_owner,
            },
        )

    @router.post("/{draft_id}/members", response_class=HTMLResponse)
    async def add_spec_member(
        request: Request,
        draft_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
        email: str = Form(...),
    ) -> HTMLResponse:
        """Add a member to a spec draft by email."""
        current_user_id, tenant_id = user_ctx
        await require_draft_owner(session, draft_id, current_user_id)

        # Find user by email, scoped to the caller's tenant. User.email is unique
        # only per tenant (uq_users_tenant_email), so an unscoped lookup could
        # match a foreign-tenant user or raise MultipleResultsFound across tenants.
        result = await session.execute(
            select(User).where(User.email == email, User.tenant_id == tenant_id)
        )
        target_user = result.scalar_one_or_none()

        if not target_user:
            # Return error message - user must log in first
            response = await _get_spec_members_html(request, draft_id, session, current_user_id)
            msg = "User not found. They must log in first before you can share."
            response.headers["HX-Trigger"] = (
                f'{{"showToast": {{"message": "{msg}", "type": "error"}}}}'
            )
            return response

        # Check if already a member
        existing = await session.execute(
            select(SpecDraftMember).where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.user_id == target_user.id,
            )
        )
        if existing.scalar_one_or_none():
            return await _get_spec_members_html(request, draft_id, session, current_user_id)

        # Add member with viewer role by default
        member = SpecDraftMember(
            spec_draft_id=draft_id,
            user_id=target_user.id,
            role=SpecDraftRole.VIEWER,
        )
        session.add(member)
        await session.commit()

        return await _get_spec_members_html(request, draft_id, session, current_user_id)

    @router.patch("/{draft_id}/members/{member_user_id}", response_class=HTMLResponse)
    async def update_spec_member_role(
        request: Request,
        draft_id: str,
        member_user_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
        role: str = Form(...),
    ) -> HTMLResponse:
        """Update a member's role in a spec draft."""
        current_user_id, _ = user_ctx
        await require_draft_owner(session, draft_id, current_user_id)

        # The role is a client-controlled string; an unrecognized value must be
        # a validation error, not an unhandled ValueError (500).
        try:
            new_role = SpecDraftRole(role)
        except ValueError:
            return HTMLResponse("<div class='error'>Invalid role</div>", status_code=400)

        result = await session.execute(
            select(SpecDraftMember).where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.user_id == member_user_id,
            )
        )
        member = result.scalar_one_or_none()

        if member:
            member.role = new_role
            await session.commit()

        return await _get_spec_members_html(request, draft_id, session, current_user_id)

    @router.delete("/{draft_id}/members/{member_user_id}", response_class=HTMLResponse)
    async def remove_spec_member(
        request: Request,
        draft_id: str,
        member_user_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> HTMLResponse:
        """Remove a member from a spec draft."""
        current_user_id, _ = user_ctx
        await require_draft_owner(session, draft_id, current_user_id)

        result = await session.execute(
            select(SpecDraftMember).where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.user_id == member_user_id,
            )
        )
        member = result.scalar_one_or_none()

        if member:
            await session.delete(member)
            await session.commit()

        return await _get_spec_members_html(request, draft_id, session, current_user_id)

    @router.delete("/{draft_id}/leave", response_class=HTMLResponse)
    async def leave_spec(
        request: Request,
        draft_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Leave a spec draft as owner (transfer ownership)."""
        current_user_id, _ = user_ctx

        # Get draft
        draft_result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
        draft = draft_result.scalar_one_or_none()
        if not draft or draft.user_id != current_user_id:
            return HTMLResponse("<div class='error'>Access denied</div>", status_code=403)

        # Check if there's another owner in members
        members_result = await session.execute(
            select(SpecDraftMember)
            .where(
                SpecDraftMember.spec_draft_id == draft_id,
                SpecDraftMember.role == SpecDraftRole.OWNER,
            )
            .options(selectinload(SpecDraftMember.user))
        )
        owner_members = list(members_result.scalars().all())

        if not owner_members:
            response = await _get_spec_members_html(request, draft_id, session, current_user_id)
            msg = "Cannot leave: assign another owner first."
            response.headers["HX-Trigger"] = (
                f'{{"showToast": {{"message": "{msg}", "type": "error"}}}}'
            )
            return response

        # Transfer ownership to the first owner member (use User.id, not keycloak_id)
        new_owner = owner_members[0]
        draft.user_id = new_owner.user.id

        # Remove the new owner from members (they're now the primary owner)
        await session.delete(new_owner)
        await session.commit()

        # Redirect to spec list since user no longer owns this
        return HTMLResponse(
            content='<div hx-redirect="/hub/spec-builder"></div>',
            headers={"HX-Redirect": "/hub/spec-builder"},
        )
