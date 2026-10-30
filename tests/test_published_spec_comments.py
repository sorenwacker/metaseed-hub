"""A published specification can be discussed by the people who can see it.

Comments existed on drafts only, so a release could not be discussed where it
is read. The thread of a published specification follows the specification's
audience: someone outside the collaboration it was published to can neither
open it nor read or write its comments, and the person who published it is
told of each comment.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.collaborations import record_memberships
from metaseed_hub.models import Notification, SpecComment, Tenant
from metaseed_hub.ui.spec_builder.routes.comment_routes import register_comment_routes
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_spec, make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio

CROPXR = "urn:mace:surf.nl:sram:group:tudelft:cropxr"


def _comment_endpoints() -> dict[str, Any]:
    router = APIRouter()
    register_comment_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    return {route.name: route.endpoint for route in router.routes}


def _request(method: str = "GET") -> Request:
    return Request(
        {"type": "http", "method": method, "path": "/", "headers": [], "query_string": b""}
    )


async def _people_and_spec(session: AsyncSession, audience: str | None) -> tuple[Any, Any, Any]:
    """A publisher, a reader in another account, and a spec published to ``audience``."""
    home, away = make_tenant(), make_tenant()
    session.add_all([home, away])
    await session.flush()
    publisher = make_user(tenant=home, email=f"publisher-{uuid4().hex[:6]}@example.org")
    reader = make_user(tenant=away, email=f"reader-{uuid4().hex[:6]}@example.org")
    session.add_all([publisher, reader])
    await session.flush()
    spec = make_spec(
        tenant=home,
        created_by=publisher,
        name="discussed",
        spec_data=SpecBuilderState(spec=ProfileSpec(name="discussed", version="1.0")).to_dict(),
    )
    spec.audience_urn = audience
    session.add(spec)
    await session.commit()
    return publisher, reader, spec


async def test_a_reader_comments_and_the_publisher_is_told(session: AsyncSession) -> None:
    publisher, reader, spec = await _people_and_spec(session, audience=None)
    endpoints = _comment_endpoints()

    response = await endpoints["add_published_spec_comment"](
        request=_request("POST"),
        spec_id=spec.id,
        session=session,
        user_ctx=(reader.id, reader.tenant_id),
        content="Why is the date optional?",
        parent_id=None,
    )

    assert response.status_code == 200
    assert "Why is the date optional?" in response.body.decode()
    stored = (await session.execute(select(SpecComment))).scalars().one()
    assert (stored.spec_id, stored.spec_draft_id) == (spec.id, None)
    told = (await session.execute(select(Notification))).scalars().all()
    assert [(n.user_id, n.kind, n.resource_kind, n.resource_id) for n in told] == [
        (publisher.id, "comment", "spec", spec.id)
    ]


async def test_someone_outside_the_audience_cannot_read_or_write(session: AsyncSession) -> None:
    _publisher, outsider, spec = await _people_and_spec(session, audience=CROPXR)
    endpoints = _comment_endpoints()

    with pytest.raises(HTTPException) as refused_read:
        await endpoints["get_published_spec_comments"](
            request=_request(),
            spec_id=spec.id,
            session=session,
            user_ctx=(outsider.id, outsider.tenant_id),
        )
    with pytest.raises(HTTPException) as refused_write:
        await endpoints["add_published_spec_comment"](
            request=_request("POST"),
            spec_id=spec.id,
            session=session,
            user_ctx=(outsider.id, outsider.tenant_id),
            content="let me in",
            parent_id=None,
        )

    assert refused_read.value.status_code == refused_write.value.status_code == 404
    assert (await session.execute(select(SpecComment))).scalars().all() == []


async def test_a_member_of_the_audience_may_comment(session: AsyncSession) -> None:
    _publisher, member, spec = await _people_and_spec(session, audience=CROPXR)
    await record_memberships(session, member.id, [CROPXR])
    await session.commit()

    response = await _comment_endpoints()["add_published_spec_comment"](
        request=_request("POST"),
        spec_id=spec.id,
        session=session,
        user_ctx=(member.id, member.tenant_id),
        content="Looks right to us",
        parent_id=None,
    )

    assert "Looks right to us" in response.body.decode()


async def test_someone_outside_the_audience_cannot_open_the_specification(
    session: AsyncSession,
) -> None:
    _publisher, outsider, spec = await _people_and_spec(session, audience=CROPXR)
    router = APIRouter()
    register_draft_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    view_spec = {route.name: route.endpoint for route in router.routes}["view_spec"]

    with pytest.raises(HTTPException) as refused:
        await view_spec(
            request=_request(),
            spec_id=spec.id,
            session=session,
            user_ctx=(outsider.id, outsider.tenant_id),
        )

    assert refused.value.status_code == 404


async def test_a_reply_cannot_attach_to_a_draft_comment(session: AsyncSession) -> None:
    publisher, reader, spec = await _people_and_spec(session, audience=None)
    draft = make_spec_draft(
        tenant=await session.get(Tenant, publisher.tenant_id), user=publisher, name="private-draft"
    )
    session.add(draft)
    await session.flush()
    draft_comment = SpecComment(spec_draft_id=draft.id, user_id=publisher.id, content="private")
    session.add(draft_comment)
    await session.commit()

    response = await _comment_endpoints()["add_published_spec_comment"](
        request=_request("POST"),
        spec_id=spec.id,
        session=session,
        user_ctx=(reader.id, reader.tenant_id),
        content="crossing threads",
        parent_id=draft_comment.id,
    )

    assert response.status_code == 404
    assert (
        await session.execute(select(SpecComment).where(SpecComment.spec_id == spec.id))
    ).scalars().all() == []


async def test_the_specification_page_offers_its_comments(session: AsyncSession) -> None:
    _publisher, reader, spec = await _people_and_spec(session, audience=None)
    templates = Jinja2Templates(directory="src/metaseed_hub/ui/templates")
    # The footer's star counts, which the app registers and a lone router lacks.
    templates.env.globals["get_repo_stars"] = lambda *args: None
    templates.env.globals["get_metaseed_stars"] = lambda *args: None
    router = APIRouter()
    register_draft_routes(router, templates)
    view_spec = {route.name: route.endpoint for route in router.routes}["view_spec"]

    response = await view_spec(
        request=_request(),
        spec_id=spec.id,
        session=session,
        user_ctx=(reader.id, reader.tenant_id),
    )

    body = response.body.decode()
    assert 'data-testid="tab-comments"' in body
    assert f'hx-post="/hub/spec-builder/spec/{spec.id}/comments"' in body
