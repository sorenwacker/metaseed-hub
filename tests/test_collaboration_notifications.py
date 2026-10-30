"""Members of a collaboration are told when something is shared with it.

Sharing with a collaboration, or publishing a specification to one, reached
its members without a word: they found the item only if they went looking.
Each member, as recorded at their last sign-in, now gets one notification.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.api import api_router
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.collaborations import record_memberships
from metaseed_hub.database import get_session
from metaseed_hub.models import Notification, SpecDraft
from metaseed_hub.notifications import open_list
from metaseed_hub.sharing import Role, add_grant, record_creator, resource_for
from metaseed_hub.ui.dependencies import ensure_tenant_and_user
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

PREFIX = "urn:mace:surf.nl:sram:group:"
CROPXR = PREFIX + "tudelft:cropxr"
PHENO = PREFIX + "tudelft:cropxr:phenotyping"
OTHER = PREFIX + "tudelft:other:members"

SPEC_PAYLOAD = {
    "spec": {
        "name": "released",
        "version": "1.0",
        "root_entity": "Sample",
        "entities": {"Sample": {"description": "a sample", "fields": []}},
    }
}

PROFILE_YAML = """
name: pushed
version: "1.0"
display_name: Pushed
description: A profile
ontology: T
root_entity: Sample
entities:
  Sample:
    description: a sample
    fields:
      - name: alias
        type: string
        required: true
"""


async def _person(session: AsyncSession, name: str, groups: list[str]):
    tenant = make_tenant(slug=f"{name}-{uuid4().hex[:6]}")
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=f"{name}@example.org", display_name=name)
    session.add(user)
    await session.flush()
    await record_memberships(session, user.id, groups)
    await session.commit()
    return tenant, user


async def _told(session: AsyncSession, user_id: str) -> list[Notification]:
    return list(
        (await session.execute(select(Notification).where(Notification.user_id == user_id)))
        .scalars()
        .all()
    )


async def test_a_grant_tells_the_members_and_nobody_else(session: AsyncSession) -> None:
    tenant, owner = await _person(session, "owner", [PHENO])
    _, colleague = await _person(session, "colleague", [PHENO])
    _, stranger = await _person(session, "stranger", [OTHER])
    dataset = make_dataset(tenant=tenant, name="field-trial")
    session.add(dataset)
    await record_creator(session, resource_for("dataset"), dataset, owner.id)
    await session.commit()

    await add_grant(
        session,
        resource_for("dataset"),
        dataset.id,
        actor_id=owner.id,
        urn=CROPXR,
        role=Role.EDITOR,
    )

    [entry] = await open_list(session, colleague.id)
    assert entry.sentence == "owner shared the dataset field-trial with cropxr as editor."
    assert entry.url == f"/hub/datasets/{dataset.id}"
    assert await _told(session, stranger.id) == []
    assert await _told(session, owner.id) == []


async def test_changing_a_grant_tells_nobody_again(session: AsyncSession) -> None:
    tenant, owner = await _person(session, "owner", [PHENO])
    _, colleague = await _person(session, "colleague", [PHENO])
    dataset = make_dataset(tenant=tenant, name="field-trial")
    session.add(dataset)
    await record_creator(session, resource_for("dataset"), dataset, owner.id)
    await session.commit()
    for role in (Role.EDITOR, Role.VIEWER):
        await add_grant(
            session, resource_for("dataset"), dataset.id, actor_id=owner.id, urn=CROPXR, role=role
        )

    assert len(await _told(session, colleague.id)) == 1


async def test_publishing_to_a_collaboration_from_the_builder(session: AsyncSession) -> None:
    tenant, author = await _person(session, "author", [PHENO])
    _, colleague = await _person(session, "colleague", [PHENO])
    _, stranger = await _person(session, "stranger", [OTHER])
    draft = SpecDraft(
        tenant_id=tenant.id,
        user_id=author.id,
        name="released",
        version="1.0",
        spec_data=SPEC_PAYLOAD,
    )
    session.add(draft)
    await record_creator(session, resource_for("draft"), draft, author.id)
    await session.commit()
    router = APIRouter()
    register_draft_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    publish = {route.name: route.endpoint for route in router.routes}["publish_draft"]

    await publish(
        request=Request(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}
        ),
        draft_id=draft.id,
        session=session,
        user_ctx=(author.id, tenant.id),
        audience_urn=CROPXR,
    )

    [entry] = await open_list(session, colleague.id)
    assert entry.sentence == "author published the specification released 1.0 to cropxr."
    assert await _told(session, stranger.id) == []


async def test_publishing_to_everyone_tells_nobody(session: AsyncSession) -> None:
    token = TokenUser(sub=f"sub-{uuid4().hex[:8]}", email="pusher@example.org", name="P", roles=[])
    _tenant, pusher = await ensure_tenant_and_user(session, token)
    await record_memberships(session, pusher.id, [PHENO])
    _, colleague = await _person(session, "colleague", [PHENO])
    await session.commit()

    async with _client(session, token) as client:
        pushed = await client.post("/api/specs", json={"yaml": PROFILE_YAML, "publish": True})

    assert pushed.status_code == 201, pushed.text
    assert await _told(session, colleague.id) == []


async def test_publishing_to_a_collaboration_over_the_api(session: AsyncSession) -> None:
    token = TokenUser(sub=f"sub-{uuid4().hex[:8]}", email="pusher@example.org", name="P", roles=[])
    _tenant, pusher = await ensure_tenant_and_user(session, token)
    await record_memberships(session, pusher.id, [PHENO])
    _, colleague = await _person(session, "colleague", [PHENO])
    await session.commit()

    async with _client(session, token) as client:
        pushed = await client.post(
            "/api/specs", json={"yaml": PROFILE_YAML, "publish": True, "audience": CROPXR}
        )

    assert pushed.status_code == 201, pushed.text
    [entry] = await open_list(session, colleague.id)
    assert entry.sentence.endswith("published the specification pushed 1.0 to cropxr.")


def _client(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
