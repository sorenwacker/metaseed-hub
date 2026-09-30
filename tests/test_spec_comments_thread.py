"""The spec comments panel renders a thread at any depth.

The panel eager-loaded one level of replies while its template recurses
without bound and reads ``comment.replies`` on every reply, so a reply to a
reply was a lazy load on the AsyncSession: MissingGreenlet, a 500 from every
route that returns the panel, and the panel stayed broken for everyone on the
draft until the reply was deleted by hand. The dataset comments panel loads the
whole thread flat and assembles it; this one now does the same.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecComment
from metaseed_hub.ui.spec_builder.routes import comment_routes
from metaseed_hub.ui.spec_builder.routes.comment_routes import register_comment_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio


def _endpoints() -> dict[str, Any]:
    router = APIRouter()
    register_comment_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    return {route.name: route.endpoint for route in router.routes}


def _get_comments() -> Any:
    return _endpoints()["get_spec_comments"]


@pytest.fixture(autouse=True)
def _draft_access_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _allow(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(comment_routes, "require_draft_access", _allow)


async def test_a_reply_to_a_reply_renders(session: AsyncSession) -> None:
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email="commenter@example.org")
    session.add(user)
    await session.flush()
    draft = make_spec_draft(
        tenant=tenant,
        user=user,
        name="threaded",
        spec_data=SpecBuilderState(spec=ProfileSpec(name="threaded", version="1.0")).to_dict(),
    )
    session.add(draft)
    await session.flush()
    root = SpecComment(spec_draft_id=draft.id, user_id=user.id, content="root remark")
    session.add(root)
    await session.flush()
    reply = SpecComment(
        spec_draft_id=draft.id, user_id=user.id, content="first reply", parent_id=root.id
    )
    session.add(reply)
    await session.flush()
    session.add(
        SpecComment(
            spec_draft_id=draft.id, user_id=user.id, content="reply to reply", parent_id=reply.id
        )
    )
    await session.commit()
    session.expunge_all()

    response = await _get_comments()(
        request=Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
        draft_id=draft.id,
        session=session,
        user_ctx=(user.id, tenant.id),
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "root remark" in body and "first reply" in body and "reply to reply" in body


async def test_a_reply_to_a_missing_parent_is_refused_not_rehomed(session: AsyncSession) -> None:
    """A well-formed ``parent_id`` naming no comment in this draft -- deleted
    between load and submit -- was stored as a new root comment with a 200.
    The dataset route answers 404 in the same case."""
    from uuid import uuid4

    from sqlalchemy import select

    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email="replier@example.org")
    session.add(user)
    await session.flush()
    draft = make_spec_draft(
        tenant=tenant,
        user=user,
        name="orphaned",
        spec_data=SpecBuilderState(spec=ProfileSpec(name="orphaned", version="1.0")).to_dict(),
    )
    session.add(draft)
    await session.commit()
    add = _endpoints()["add_spec_comment"]

    response = await add(
        request=Request({"type": "http", "method": "POST", "path": "/", "headers": []}),
        draft_id=draft.id,
        session=session,
        user_ctx=(user.id, tenant.id),
        content="late reply",
        parent_id=str(uuid4()),
    )

    assert response.status_code == 404
    stored = (await session.execute(select(SpecComment))).scalars().all()
    assert stored == [], "nothing was posted in its place"
