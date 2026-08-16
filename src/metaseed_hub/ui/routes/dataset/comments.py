"""Dataset comment and reaction routes."""

import logging
import uuid
from typing import Annotated

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from metaseed_hub.models import (
    Comment,
    CommentReaction,
    ReactionType,
    User,
)
from metaseed_hub.ui.dependencies import (
    CurrentUser,
    DbSession,
    get_dataset_for_user,
)
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

from ._router import router

logger = logging.getLogger("metaseed_hub")


async def _get_comments_html(
    request: Request,
    dataset_id: str,
    session: DbSession,
    keycloak_sub: str,
    offset: int = 0,
    limit: int = 20,
) -> Response:
    """Render the comments list partial."""
    # Get database user ID from keycloak sub
    user_result = await session.execute(select(User).where(User.keycloak_id == keycloak_sub))
    db_user = user_result.scalar_one_or_none()
    current_user_id = db_user.id if db_user else None

    # Get total count of top-level comments
    count_result = await session.execute(
        select(func.count(Comment.id)).where(
            Comment.dataset_id == dataset_id, Comment.parent_id.is_(None)
        )
    )
    total_count = count_result.scalar() or 0

    # The whole thread in one query, then assembled here. The template's reply
    # macro recurses without bound while eager loading stopped at two levels,
    # so a reply at depth 3 was a lazy load on an AsyncSession — MissingGreenlet,
    # a 500. Loading flat removes the coupling instead of moving the cliff.
    thread = await session.execute(
        select(Comment)
        .where(Comment.dataset_id == dataset_id)
        .options(selectinload(Comment.user), selectinload(Comment.reactions))
        .order_by(Comment.created_at.desc())
    )
    every = list(thread.scalars().all())

    children: dict[str, list[Comment]] = {}
    for comment in every:
        if comment.parent_id:
            children.setdefault(str(comment.parent_id), []).append(comment)
    for comment in every:
        # Set on the loaded instances, so the template never triggers a load
        # at any depth.
        comment.__dict__["replies"] = children.get(str(comment.id), [])

    roots = [c for c in every if c.parent_id is None]
    comments = roots[offset : offset + limit]

    has_more = (offset + len(comments)) < total_count
    next_offset = offset + limit

    return render_template(
        request=request,
        name="partials/comments_list.html",
        context={
            "comments": comments,
            "dataset_id": dataset_id,
            "current_user_id": current_user_id,
            "has_more": has_more,
            "next_offset": next_offset,
            "is_load_more": offset > 0,
        },
    )


@router.get("/{dataset_id}/comments", response_class=HTMLResponse)
async def get_dataset_comments(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
    offset: int = 0,
) -> Response:
    """Get all comments for a dataset."""
    await get_dataset_for_user(dataset_id, session, user)
    return await _get_comments_html(request, dataset_id, session, user.sub, offset=offset)


@router.post("/{dataset_id}/comments", response_class=HTMLResponse)
async def add_dataset_comment(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
    content: Annotated[str, Form()],
    parent_id: Annotated[str | None, Form()] = None,
) -> Response:
    """Add a comment to a dataset."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    await get_dataset_for_user(dataset_id, session, user)

    # Get user from database by keycloak sub
    user_result = await session.execute(select(User).where(User.keycloak_id == user.sub))
    db_user = user_result.scalar_one_or_none()

    if not db_user:
        return HTMLResponse("<div class='error'>User not found</div>", status_code=400)

    # A reply must attach to a comment that exists and belongs to this dataset.
    # Without the scope check a caller could attach a reply under another
    # dataset's thread; without the existence check the foreign key raises an
    # unhandled IntegrityError on commit. The id is parsed first because the
    # column is UUID-typed and a malformed value would fail at the query.
    if parent_id:
        try:
            uuid.UUID(parent_id)
        except ValueError:
            return HTMLResponse(
                "<div class='error'>Parent comment not found</div>", status_code=404
            )
        parent_result = await session.execute(
            select(Comment).where(Comment.id == parent_id, Comment.dataset_id == dataset_id)
        )
        if parent_result.scalar_one_or_none() is None:
            return HTMLResponse(
                "<div class='error'>Parent comment not found</div>", status_code=404
            )

    comment = Comment(
        dataset_id=dataset_id,
        user_id=db_user.id,
        parent_id=parent_id if parent_id else None,
        content=content.strip(),
    )
    session.add(comment)
    await session.commit()

    return await _get_comments_html(request, dataset_id, session, user.sub)


@router.delete("/{dataset_id}/comments/{comment_id}", response_class=HTMLResponse)
async def delete_dataset_comment(
    request: Request,
    dataset_id: str,
    comment_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Delete a comment (only by owner)."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    await get_dataset_for_user(dataset_id, session, user)

    # Get user from database
    user_result = await session.execute(select(User).where(User.keycloak_id == user.sub))
    db_user = user_result.scalar_one_or_none()

    if not db_user:
        return HTMLResponse("<div class='error'>User not found</div>", status_code=400)

    # Find comment and verify ownership. Scope by dataset_id so a comment can
    # only be deleted through the dataset it actually belongs to; the URL grant
    # is for dataset_id, but the comment was previously resolved globally.
    result = await session.execute(
        select(Comment).where(Comment.id == comment_id, Comment.dataset_id == dataset_id)
    )
    comment = result.scalar_one_or_none()

    if comment and comment.user_id == db_user.id:
        await session.delete(comment)
        await session.commit()

    return await _get_comments_html(request, dataset_id, session, user.sub)


@router.post("/{dataset_id}/comments/{comment_id}/react", response_class=HTMLResponse)
async def react_to_comment(
    request: Request,
    dataset_id: str,
    comment_id: str,
    session: DbSession,
    user: CurrentUser,
    reaction: Annotated[str, Form()],
) -> Response:
    """Add or toggle a reaction on a comment."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    await get_dataset_for_user(dataset_id, session, user)

    # Get user from database
    user_result = await session.execute(select(User).where(User.keycloak_id == user.sub))
    db_user = user_result.scalar_one_or_none()

    if not db_user:
        return HTMLResponse("<div class='error'>User not found</div>", status_code=400)

    # Confirm the comment belongs to the URL dataset before reacting. Without
    # this, a user with access to one dataset could toggle reactions on another
    # dataset's comment by supplying its comment_id.
    comment_result = await session.execute(
        select(Comment).where(Comment.id == comment_id, Comment.dataset_id == dataset_id)
    )
    if comment_result.scalar_one_or_none() is None:
        return HTMLResponse("<div class='error'>Comment not found</div>", status_code=404)

    # Check for existing reaction
    existing_result = await session.execute(
        select(CommentReaction).where(
            CommentReaction.comment_id == comment_id,
            CommentReaction.user_id == db_user.id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    try:
        reaction_type = ReactionType(reaction)
    except ValueError:
        return HTMLResponse("<div class='error'>Invalid reaction</div>", status_code=400)

    if existing:
        if existing.reaction == reaction_type:
            # Toggle off - remove reaction
            await session.delete(existing)
        else:
            # Change reaction type
            existing.reaction = reaction_type
    else:
        # Add new reaction
        new_reaction = CommentReaction(
            comment_id=comment_id,
            user_id=db_user.id,
            reaction=reaction_type,
        )
        session.add(new_reaction)

    await session.commit()

    return await _get_comments_html(request, dataset_id, session, user.sub)


# =============================================================================
# Version History Routes
# =============================================================================
