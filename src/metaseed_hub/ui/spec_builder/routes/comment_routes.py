"""Comment and reaction routes for spec builder."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from metaseed_hub.models import ReactionType, SpecComment, SpecCommentReaction
from metaseed_hub.ui.spec_builder.access import require_draft_access

from ._common import SessionDep, UserContextDep

__all__ = ["register_comment_routes"]


def register_comment_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register comment and reaction routes."""

    async def _get_spec_comments_html(
        request: Request,
        draft_id: str,
        session: AsyncSession,
        user_id: str,
    ) -> HTMLResponse:
        """Render the spec comments list partial.

        Args:
            request: FastAPI request
            draft_id: Draft ID
            session: Database session
            user_id: Database User.id (not keycloak_id)
        """
        # Get top-level comments (no parent) with nested relationships
        result = await session.execute(
            select(SpecComment)
            .where(SpecComment.spec_draft_id == draft_id, SpecComment.parent_id.is_(None))
            .options(
                selectinload(SpecComment.user),
                selectinload(SpecComment.reactions),
                selectinload(SpecComment.replies).selectinload(SpecComment.user),
                selectinload(SpecComment.replies).selectinload(SpecComment.reactions),
            )
            .order_by(SpecComment.created_at.desc())
        )
        comments = list(result.scalars().all())

        return templates.TemplateResponse(
            request,
            "partials/spec_comments_list.html",
            {
                "comments": comments,
                "draft_id": draft_id,
                "current_user_id": user_id,
            },
        )

    @router.get("/{draft_id}/comments", response_class=HTMLResponse)
    async def get_spec_comments(
        request: Request,
        draft_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Get all comments for a spec draft."""
        user_id, _ = user_ctx
        await require_draft_access(session, draft_id, user_id)

        return await _get_spec_comments_html(request, draft_id, session, user_id)

    @router.post("/{draft_id}/comments", response_class=HTMLResponse)
    async def add_spec_comment(
        request: Request,
        draft_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
        content: str = Form(...),
        parent_id: str | None = Form(None),
    ) -> Response:
        """Add a comment to a spec draft."""
        user_id, _ = user_ctx
        await require_draft_access(session, draft_id, user_id)

        comment = SpecComment(
            spec_draft_id=draft_id,
            user_id=user_id,
            parent_id=parent_id if parent_id else None,
            content=content.strip(),
        )
        session.add(comment)
        await session.commit()

        return await _get_spec_comments_html(request, draft_id, session, user_id)

    @router.delete("/{draft_id}/comments/{comment_id}", response_class=HTMLResponse)
    async def delete_spec_comment(
        request: Request,
        draft_id: str,
        comment_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Delete a spec comment (only by owner)."""
        user_id, _ = user_ctx

        # Find comment and verify ownership
        result = await session.execute(select(SpecComment).where(SpecComment.id == comment_id))
        comment = result.scalar_one_or_none()

        if comment and comment.user_id == user_id:
            await session.delete(comment)
            await session.commit()

        return await _get_spec_comments_html(request, draft_id, session, user_id)

    @router.post("/{draft_id}/comments/{comment_id}/react", response_class=HTMLResponse)
    async def react_to_spec_comment(
        request: Request,
        draft_id: str,
        comment_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
        reaction: str = Form(...),
    ) -> Response:
        """Add or toggle a reaction on a spec comment."""
        user_id, _ = user_ctx
        await require_draft_access(session, draft_id, user_id)

        # Check for existing reaction
        existing_result = await session.execute(
            select(SpecCommentReaction).where(
                SpecCommentReaction.comment_id == comment_id,
                SpecCommentReaction.user_id == user_id,
            )
        )
        existing = existing_result.scalar_one_or_none()

        reaction_type = ReactionType(reaction)

        if existing:
            if existing.reaction == reaction_type:
                # Toggle off - remove reaction
                await session.delete(existing)
            else:
                # Change reaction type
                existing.reaction = reaction_type
        else:
            # Add new reaction
            new_reaction = SpecCommentReaction(
                comment_id=comment_id,
                user_id=user_id,
                reaction=reaction_type,
            )
            session.add(new_reaction)

        await session.commit()

        return await _get_spec_comments_html(request, draft_id, session, user_id)
