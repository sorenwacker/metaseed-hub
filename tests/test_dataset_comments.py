"""Tests for dataset comment routes: parent scoping and reaction validation.

A reply's parent must exist and belong to the same dataset the URL grants
access to, mirroring the scoped lookups delete and react already perform.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Comment, CommentReaction, Dataset, User
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes.dataset import comments as comments_module
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


@pytest.fixture(autouse=True)
def _fake_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace template rendering with a stub; these tests assert on data."""

    def _render(*, request: object, name: str, context: dict) -> Response:
        return Response("rendered")

    monkeypatch.setattr(comments_module, "render_template", _render)


async def _caller(session: AsyncSession) -> tuple[Dataset, User, TokenUser]:
    """A tenant, its user, and a dataset the user can access."""
    sub = f"commenter-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    db_user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(db_user)
    await session.flush()
    dataset = make_dataset(tenant=tenant)
    session.add(dataset)
    await session.commit()
    token = TokenUser(sub=sub, email=db_user.email, name="C", roles=[])
    return dataset, db_user, token


async def _comments_in(session: AsyncSession, dataset_id: str) -> list[Comment]:
    result = await session.execute(select(Comment).where(Comment.dataset_id == dataset_id))
    return list(result.scalars().all())


async def test_top_level_comment_is_created(session: AsyncSession) -> None:
    dataset, _, token = await _caller(session)

    response = await comments_module.add_dataset_comment(
        _csrf_request(), dataset.id, session, token, content="hello"
    )

    assert response.status_code == 200
    comments = await _comments_in(session, dataset.id)
    assert [c.content for c in comments] == ["hello"]


async def test_reply_to_missing_parent_is_rejected(session: AsyncSession) -> None:
    """A nonexistent parent_id previously raised an IntegrityError (500)."""
    dataset, _, token = await _caller(session)

    response = await comments_module.add_dataset_comment(
        _csrf_request(), dataset.id, session, token, content="reply", parent_id=str(uuid4())
    )

    assert response.status_code == 404
    assert await _comments_in(session, dataset.id) == []


async def test_reply_with_malformed_parent_id_is_rejected(session: AsyncSession) -> None:
    """A non-UUID parent_id must not reach the UUID-typed column query."""
    dataset, _, token = await _caller(session)

    response = await comments_module.add_dataset_comment(
        _csrf_request(), dataset.id, session, token, content="reply", parent_id="no-such-id"
    )

    assert response.status_code == 404
    assert await _comments_in(session, dataset.id) == []


async def test_reply_cannot_attach_to_another_datasets_comment(session: AsyncSession) -> None:
    """The parent lookup is scoped by dataset, closing the cross-dataset hole."""
    dataset_a, _, token = await _caller(session)
    dataset_b, user_b, token_b = await _caller(session)
    await comments_module.add_dataset_comment(
        _csrf_request(), dataset_b.id, session, token_b, content="thread in B"
    )
    (parent_b,) = await _comments_in(session, dataset_b.id)

    response = await comments_module.add_dataset_comment(
        _csrf_request(), dataset_a.id, session, token, content="sneaky", parent_id=parent_b.id
    )

    assert response.status_code == 404
    assert await _comments_in(session, dataset_a.id) == []
    assert len(await _comments_in(session, dataset_b.id)) == 1


async def test_reply_to_same_dataset_parent_is_accepted(session: AsyncSession) -> None:
    dataset, _, token = await _caller(session)
    await comments_module.add_dataset_comment(
        _csrf_request(), dataset.id, session, token, content="parent"
    )
    (parent,) = await _comments_in(session, dataset.id)

    response = await comments_module.add_dataset_comment(
        _csrf_request(), dataset.id, session, token, content="reply", parent_id=parent.id
    )

    assert response.status_code == 200
    comments = {c.content: c for c in await _comments_in(session, dataset.id)}
    assert comments["reply"].parent_id == parent.id


async def test_invalid_reaction_value_is_rejected(session: AsyncSession) -> None:
    """An unknown reaction value previously raised ValueError (500)."""
    dataset, _, token = await _caller(session)
    await comments_module.add_dataset_comment(
        _csrf_request(), dataset.id, session, token, content="c"
    )
    (comment,) = await _comments_in(session, dataset.id)

    response = await comments_module.react_to_comment(
        _csrf_request(), dataset.id, comment.id, session, token, reaction="banana"
    )

    assert response.status_code == 400
    reactions = await session.execute(
        select(CommentReaction).where(CommentReaction.comment_id == comment.id)
    )
    assert list(reactions.scalars().all()) == []


async def test_valid_reaction_is_recorded(session: AsyncSession) -> None:
    dataset, db_user, token = await _caller(session)
    await comments_module.add_dataset_comment(
        _csrf_request(), dataset.id, session, token, content="c"
    )
    (comment,) = await _comments_in(session, dataset.id)

    response = await comments_module.react_to_comment(
        _csrf_request(), dataset.id, comment.id, session, token, reaction="like"
    )

    assert response.status_code == 200
    reactions = await session.execute(
        select(CommentReaction).where(CommentReaction.comment_id == comment.id)
    )
    assert [r.user_id for r in reactions.scalars().all()] == [db_user.id]
