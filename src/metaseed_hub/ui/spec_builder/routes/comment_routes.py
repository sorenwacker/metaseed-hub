"""Comment and reaction routes for spec drafts and published specifications.

The two threads work the same way and differ in three things: the column a
comment names its subject by, who may take part, and who is told. A
:class:`_Thread` carries those, and one set of handlers serves both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from metaseed_hub.models import ReactionType, SpecComment, SpecCommentReaction
from metaseed_hub.notifications import comment_posted
from metaseed_hub.sharing import resource_for
from metaseed_hub.ui.spec_builder.access import require_draft_access, require_visible_spec

from ._common import SessionDep, UserContextDep

__all__ = ["register_comment_routes"]


def _is_comment_id(value: str) -> bool:
    """Whether `value` could name a comment.

    SpecComment ids are UUID-typed columns, so a malformed value raises a
    DBAPIError on Postgres rather than matching nothing. Checking the syntax
    first turns that 500 into the 404 it always was.

    Args:
        value: A client-supplied comment id.

    Returns:
        True when the value parses as a UUID.
    """
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class _Thread:
    """One subject's comments: a draft's or a published specification's.

    Attributes:
        column: The SpecComment column naming the subject.
        subject_id: The draft or specification.
        url: Where the thread's routes live, for the template's buttons.
        resource_kind: The sharing kind the notifications name.
        publisher_id: Who published a specification, told of each comment.
    """

    column: Any
    subject_id: str
    url: str
    resource_kind: str
    publisher_id: str | None = None

    def owns(self, comment_id: str) -> Any:
        """The condition selecting ``comment_id`` within this thread only."""
        return (SpecComment.id == comment_id) & (self.column == self.subject_id)


async def _draft_thread(session: AsyncSession, draft_id: str, user_id: str) -> _Thread:
    await require_draft_access(session, draft_id, user_id)
    return _Thread(SpecComment.spec_draft_id, draft_id, f"/hub/spec-builder/{draft_id}", "draft")


async def _spec_thread(session: AsyncSession, spec_id: str, user_id: str) -> _Thread:
    spec = await require_visible_spec(session, spec_id, user_id)
    return _Thread(
        SpecComment.spec_id,
        spec_id,
        f"/hub/spec-builder/spec/{spec_id}",
        "spec",
        publisher_id=spec.created_by_id,
    )


def register_comment_routes(router: APIRouter, templates: Jinja2Templates) -> None:
    """Register comment and reaction routes for drafts and published specifications."""

    async def _thread_html(
        request: Request, thread: _Thread, session: AsyncSession, user_id: str
    ) -> HTMLResponse:
        """Render the comments list partial of one thread.

        Args:
            request: FastAPI request
            thread: Whose comments
            session: Database session
            user_id: Database User.id (not keycloak_id)
        """
        # The whole thread in one query, then assembled here, as the dataset
        # panel does. Eager loading stopped one level down while the template
        # recurses without bound and reads ``replies`` on every reply, so a
        # reply to a reply was a lazy load on the AsyncSession: MissingGreenlet,
        # a 500 from every route that returns this panel.
        result = await session.execute(
            select(SpecComment)
            .where(thread.column == thread.subject_id)
            .options(selectinload(SpecComment.user), selectinload(SpecComment.reactions))
            .order_by(SpecComment.created_at.desc())
        )
        every = list(result.scalars().all())
        children: dict[str, list[SpecComment]] = {}
        for comment in every:
            if comment.parent_id:
                children.setdefault(str(comment.parent_id), []).append(comment)
        for comment in every:
            # Set on the loaded instances, so the template never triggers a
            # load at any depth.
            comment.__dict__["replies"] = children.get(str(comment.id), [])
        comments = [c for c in every if c.parent_id is None]

        return templates.TemplateResponse(
            request,
            "partials/spec_comments_list.html",
            {
                "comments": comments,
                "comments_url": thread.url,
                "current_user_id": user_id,
            },
        )

    async def _add(
        request: Request,
        thread: _Thread,
        session: AsyncSession,
        user_id: str,
        content: str,
        parent_id: str | None,
    ) -> Response:
        # Only accept parent_id if it names a comment in THIS thread, so a
        # client-supplied parent cannot link a reply across drafts, or from a
        # release into a private draft.
        resolved_parent_id: str | None = None
        parent_author_id: str | None = None
        if parent_id:
            if not _is_comment_id(parent_id):
                return HTMLResponse(
                    "<div class='error'>Parent comment not found</div>", status_code=404
                )
            parent = (
                await session.execute(select(SpecComment).where(thread.owns(parent_id)))
            ).scalar_one_or_none()
            if parent is None:
                # Deleted between load and submit, or never in this thread: a
                # 404 as the dataset route answers, not a new root comment
                # posted in its place with a 200.
                return HTMLResponse(
                    "<div class='error'>Parent comment not found</div>", status_code=404
                )
            resolved_parent_id = parent.id
            parent_author_id = parent.user_id

        comment = SpecComment(
            user_id=user_id, parent_id=resolved_parent_id, content=content.strip()
        )
        setattr(comment, thread.column.key, thread.subject_id)
        session.add(comment)
        await comment_posted(
            session,
            resource_for(thread.resource_kind),
            thread.subject_id,
            actor_id=user_id,
            parent_author_id=parent_author_id,
            publisher_id=thread.publisher_id,
        )
        await session.commit()

        return await _thread_html(request, thread, session, user_id)

    async def _delete(
        request: Request, thread: _Thread, session: AsyncSession, user_id: str, comment_id: str
    ) -> Response:
        if not _is_comment_id(comment_id):
            return HTMLResponse("<div class='error'>Comment not found</div>", status_code=404)

        # Scoped to the thread, so a comment can only be deleted through the
        # subject it belongs to, and only by its author.
        comment = (
            await session.execute(select(SpecComment).where(thread.owns(comment_id)))
        ).scalar_one_or_none()
        if comment and comment.user_id == user_id:
            await session.delete(comment)
            await session.commit()

        return await _thread_html(request, thread, session, user_id)

    async def _react(
        request: Request,
        thread: _Thread,
        session: AsyncSession,
        user_id: str,
        comment_id: str,
        reaction: str,
    ) -> Response:
        # The reaction is a client-controlled string; an unrecognized value
        # must be a validation error, not an unhandled ValueError (500).
        try:
            reaction_type = ReactionType(reaction)
        except ValueError:
            return HTMLResponse("<div class='error'>Invalid reaction</div>", status_code=400)

        if not _is_comment_id(comment_id):
            return HTMLResponse("<div class='error'>Comment not found</div>", status_code=404)

        # Confirm the comment belongs to this thread before reacting. Without
        # this someone in one thread could toggle a reaction on another's
        # comment by supplying its comment_id.
        if (
            await session.execute(select(SpecComment).where(thread.owns(comment_id)))
        ).scalar_one_or_none() is None:
            return HTMLResponse("<div class='error'>Comment not found</div>", status_code=404)

        existing = (
            await session.execute(
                select(SpecCommentReaction).where(
                    SpecCommentReaction.comment_id == comment_id,
                    SpecCommentReaction.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

        if existing:
            if existing.reaction == reaction_type:
                # Toggle off - remove reaction
                await session.delete(existing)
            else:
                # Change reaction type
                existing.reaction = reaction_type
        else:
            session.add(
                SpecCommentReaction(comment_id=comment_id, user_id=user_id, reaction=reaction_type)
            )

        await session.commit()

        return await _thread_html(request, thread, session, user_id)

    @router.get("/{draft_id}/comments", response_class=HTMLResponse)
    async def get_spec_comments(
        request: Request, draft_id: str, session: SessionDep, user_ctx: UserContextDep
    ) -> Response:
        """Get all comments for a spec draft."""
        user_id, _ = user_ctx
        thread = await _draft_thread(session, draft_id, user_id)
        return await _thread_html(request, thread, session, user_id)

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
        thread = await _draft_thread(session, draft_id, user_id)
        return await _add(request, thread, session, user_id, content, parent_id)

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
        thread = await _draft_thread(session, draft_id, user_id)
        return await _delete(request, thread, session, user_id, comment_id)

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
        thread = await _draft_thread(session, draft_id, user_id)
        return await _react(request, thread, session, user_id, comment_id, reaction)

    @router.get("/spec/{spec_id}/comments", response_class=HTMLResponse)
    async def get_published_spec_comments(
        request: Request, spec_id: str, session: SessionDep, user_ctx: UserContextDep
    ) -> Response:
        """Get all comments on a published specification."""
        user_id, _ = user_ctx
        thread = await _spec_thread(session, spec_id, user_id)
        return await _thread_html(request, thread, session, user_id)

    @router.post("/spec/{spec_id}/comments", response_class=HTMLResponse)
    async def add_published_spec_comment(
        request: Request,
        spec_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
        content: str = Form(...),
        parent_id: str | None = Form(None),
    ) -> Response:
        """Add a comment to a published specification."""
        user_id, _ = user_ctx
        thread = await _spec_thread(session, spec_id, user_id)
        return await _add(request, thread, session, user_id, content, parent_id)

    @router.delete("/spec/{spec_id}/comments/{comment_id}", response_class=HTMLResponse)
    async def delete_published_spec_comment(
        request: Request,
        spec_id: str,
        comment_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
    ) -> Response:
        """Delete a comment on a published specification (only by its author)."""
        user_id, _ = user_ctx
        thread = await _spec_thread(session, spec_id, user_id)
        return await _delete(request, thread, session, user_id, comment_id)

    @router.post("/spec/{spec_id}/comments/{comment_id}/react", response_class=HTMLResponse)
    async def react_to_published_spec_comment(
        request: Request,
        spec_id: str,
        comment_id: str,
        session: SessionDep,
        user_ctx: UserContextDep,
        reaction: str = Form(...),
    ) -> Response:
        """Add or toggle a reaction on a comment on a published specification."""
        user_id, _ = user_ctx
        thread = await _spec_thread(session, spec_id, user_id)
        return await _react(request, thread, session, user_id, comment_id, reaction)
