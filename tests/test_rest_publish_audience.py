"""A client pushing a profile can publish it to a collaboration.

The browser can choose an audience; a metaseed instance pushing over a token
could not, so the only release it could make was a hub-wide one. The REST
contract carries the same choice and the same refusal, and `/api/me` says
which collaborations the token may choose from -- a client cannot offer a
list it has no way to obtain.
"""

from __future__ import annotations

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
from metaseed_hub.collaborations import record_memberships
from metaseed_hub.database import get_session
from metaseed_hub.models import Spec
from metaseed_hub.ui.dependencies import ensure_tenant_and_user

pytestmark = pytest.mark.asyncio

PREFIX = "urn:mace:surf.nl:sram:group:"
CROPXR = PREFIX + "tudelft:cropxr"
PHENO = PREFIX + "tudelft:cropxr:phenotyping"
OTHER = PREFIX + "tudelft:other:members"

PROFILE_YAML = """
name: audience-probe
version: "1.0"
display_name: Audience probe
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


@pytest.fixture
async def caller(session: AsyncSession):
    """A signed-in account in the cropxr phenotyping group."""
    token = TokenUser(sub=f"sub-{uuid4().hex[:8]}", email="pusher@example.org", name="P", roles=[])
    _tenant, user = await ensure_tenant_and_user(session, token)
    await record_memberships(session, user.id, [PHENO])
    await session.commit()
    return token


def _client(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_me_names_the_collaborations_a_client_may_publish_to(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _client(session, caller) as client:
        body = (await client.get("/api/me")).json()

    assert [c["urn"] for c in body["collaborations"]] == [CROPXR]
    assert body["collaborations"][0]["name"] == "cropxr"
    assert body["collaborations"][0]["groups"] == ["phenotyping"]


async def test_publishing_to_a_collaboration_over_the_api(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _client(session, caller) as client:
        pushed = await client.post(
            "/api/specs", json={"yaml": PROFILE_YAML, "publish": True, "audience": CROPXR}
        )

    assert pushed.status_code == 201, pushed.text
    assert pushed.json()["audience"] == CROPXR
    row = (await session.execute(select(Spec))).scalar_one()
    assert row.audience_urn == CROPXR


async def test_publishing_without_an_audience_still_means_everyone(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _client(session, caller) as client:
        pushed = await client.post("/api/specs", json={"yaml": PROFILE_YAML, "publish": True})

    assert pushed.status_code == 201, pushed.text
    assert pushed.json()["audience"] is None
    row = (await session.execute(select(Spec))).scalar_one()
    assert row.audience_urn is None


async def test_publishing_to_a_collaboration_you_are_not_in_is_refused(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _client(session, caller) as client:
        refused = await client.post(
            "/api/specs", json={"yaml": PROFILE_YAML, "publish": True, "audience": OTHER}
        )

    assert refused.status_code == 403, refused.text
    assert "other" in refused.json()["detail"]
    assert (await session.execute(select(Spec))).scalar_one_or_none() is None


async def test_an_audience_on_a_draft_push_is_refused(
    session: AsyncSession, caller: TokenUser
) -> None:
    """A draft has no audience: it is private, and shared per person or per
    collaboration instead. Accepting the field and ignoring it would lie."""
    async with _client(session, caller) as client:
        refused = await client.post("/api/specs", json={"yaml": PROFILE_YAML, "audience": CROPXR})

    assert refused.status_code == 422, refused.text
