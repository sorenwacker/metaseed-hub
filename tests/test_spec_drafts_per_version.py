"""A draft is one name at one version; several versions of a name coexist.

Drafts were unique per name, so a push of the next version replaced the
previous one and a person could not hold 1.2 and 1.3 of a profile at once,
although their specs directory does exactly that.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.api import api_router
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import SpecDraft
from metaseed_hub.ui.dependencies import ensure_tenant_and_user, tenant_slug_for
from metaseed_hub.ui.spec_builder.access import create_new_draft, free_draft_name
from tests.factories import make_spec_draft, make_tenant, make_user
from tests.mcp_helpers import _calling_with, _drafting, _tool

pytestmark = pytest.mark.asyncio

PROFILE_YAML = """
name: cropxr-phenotyping
version: "1.2"
display_name: CropXR phenotyping
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


def _at(version: str) -> str:
    return re.sub(r"^version:.*$", f'version: "{version}"', PROFILE_YAML, flags=re.MULTILINE)


def _client(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def caller(session: AsyncSession) -> TokenUser:
    token = TokenUser(sub=f"sub-{uuid4().hex[:8]}", email="p@example.org", name="P", roles=[])
    await ensure_tenant_and_user(session, token)
    return token


async def test_pushing_two_versions_keeps_both_as_drafts(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _client(session, caller) as client:
        first = await client.post("/api/specs", json={"yaml": _at("1.2")})
        second = await client.post("/api/specs", json={"yaml": _at("1.3")})
        listed = await client.get("/api/specs")
    assert (first.status_code, second.status_code) == (201, 201)
    drafts = sorted((d["name"], d["version"]) for d in listed.json() if d["visibility"] == "draft")
    assert drafts == [("cropxr-phenotyping", "1.2"), ("cropxr-phenotyping", "1.3")]


async def test_pushing_a_version_again_updates_that_draft_only(
    session: AsyncSession, caller: TokenUser
) -> None:
    changed = _at("1.3").replace("required: true", "required: false")
    async with _client(session, caller) as client:
        await client.post("/api/specs", json={"yaml": _at("1.2")})
        before = await client.post("/api/specs", json={"yaml": _at("1.3")})
        after = await client.post("/api/specs", json={"yaml": changed})
    assert after.status_code == 200
    assert after.json()["content_hash"] != before.json()["content_hash"]
    rows = (await session.execute(select(SpecDraft).order_by(SpecDraft.version))).scalars().all()
    assert [r.version for r in rows] == ["1.2", "1.3"]


async def _account(session: AsyncSession):
    sub = f"sub-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(user)
    await session.commit()
    return tenant, user


def _spec(version: str):
    from metaseed.specs.schema import ProfileSpec

    return ProfileSpec(
        version=version,
        name="two-versions",
        display_name="Two",
        description="d",
        ontology="T",
        root_entity="Sample",
        entities={},
    )


async def test_the_builder_holds_one_name_at_two_versions(session: AsyncSession) -> None:
    tenant, user = await _account(session)
    one = await create_new_draft(
        session, user_id=user.id, tenant_id=tenant.id, name="two-versions", spec=_spec("1.0")
    )
    two = await create_new_draft(
        session, user_id=user.id, tenant_id=tenant.id, name="two-versions", spec=_spec("2.0")
    )
    assert one.id != two.id
    assert {d.version for d in (one, two)} == {"1.0", "2.0"}


async def test_a_name_is_only_taken_at_its_version(session: AsyncSession) -> None:
    tenant, user = await _account(session)
    session.add(make_spec_draft(tenant=tenant, user=user, name="held", version="1.0"))
    await session.commit()

    same = await free_draft_name(
        session, user_id=user.id, tenant_id=tenant.id, wanted="held", version="1.0"
    )
    other = await free_draft_name(
        session, user_id=user.id, tenant_id=tenant.id, wanted="held", version="1.1"
    )
    assert same == "held-2"
    assert other == "held"


async def test_an_agent_names_a_version_when_the_name_alone_is_ambiguous(
    server, session: AsyncSession
) -> None:
    secret = await _drafting(server, session, slug="ver00001", name="Twice")
    existing = (await session.execute(select(SpecDraft))).scalar_one()
    tenant_id, user_id = existing.tenant_id, existing.user_id
    from metaseed_hub.models import Tenant, User

    session.add(
        make_spec_draft(
            tenant=await session.get(Tenant, tenant_id),
            user=await session.get(User, user_id),
            name="Twice",
            version="2.0",
            spec_data={"spec": _spec("2.0").model_dump(mode="json") | {"name": "Twice"}},
        )
    )
    await session.commit()
    status = await _tool(server, "spec_status")

    with _calling_with(secret), pytest.raises(ValueError, match="1.0, 2.0"):
        await status("Twice")
    with _calling_with(secret):
        summary = json.loads(await status("Twice@2.0"))
    assert summary["version"] == "2.0"
