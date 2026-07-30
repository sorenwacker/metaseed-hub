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

        # Only accept parent_id if it names a comment in THIS draft, so a
        # client-supplied parent cannot link a reply across drafts. Matches the
        # spec_draft_id scoping the sibling delete/react routes enforce.
        resolved_parent_id: str | None = None
        if parent_id:
            parent = (
                await session.execute(
                    select(SpecComment).where(
                        SpecComment.id == parent_id,
                        SpecComment.spec_draft_id == draft_id,
                    )
                )
            ).scalar_one_or_none()
            resolved_parent_id = parent.id if parent else None

        comment = SpecComment(
            spec_draft_id=draft_id,
            user_id=user_id,
            parent_id=resolved_parent_id,
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
        await require_draft_access(session, draft_id, user_id)

        # Find comment and verify ownership. Scope by spec_draft_id so the
        # comment can only be deleted through the draft it belongs to, matching
        # the sibling comment routes which all gate on require_draft_access.
        result = await session.execute(
            select(SpecComment).where(
                SpecComment.id == comment_id, SpecComment.spec_draft_id == draft_id
            )
        )
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

        # The reaction is a client-controlled string; an unrecognized value
        # must be a validation error, not an unhandled ValueError (500).
        try:
            reaction_type = ReactionType(reaction)
        except ValueError:
            return HTMLResponse("<div class='error'>Invalid reaction</div>", status_code=400)

        # Confirm the comment belongs to the URL draft before reacting, mirroring
        # delete_spec_comment. Without this a member of one draft could toggle a
        # reaction on another draft's comment by supplying its comment_id.
        comment_result = await session.execute(
            select(SpecComment).where(
                SpecComment.id == comment_id, SpecComment.spec_draft_id == draft_id
            )
        )
        if comment_result.scalar_one_or_none() is None:
            return HTMLResponse("<div class='error'>Comment not found</div>", status_code=404)

        # Check for existing reaction
        existing_result = await session.execute(
            select(SpecCommentReaction).where(
                SpecCommentReaction.comment_id == comment_id,
                SpecCommentReaction.user_id == user_id,
            )
        )
        existing = existing_result.scalar_one_or_none()

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
