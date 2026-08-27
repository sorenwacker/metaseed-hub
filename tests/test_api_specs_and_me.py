"""The REST API lets a client learn whose token it holds and exchange specs.

A metaseed instance pushing a profile or a dataset to the hub holds a personal
access token and nothing else: it cannot know the tenant the token acts in,
and until now the only way to publish a specification was the browser. These
routes are what that client uses; the publish gate is the same one the spec
builder applies.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.api import api_router
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Spec, SpecDraft, SpecStatus, User
from metaseed_hub.ui.dependencies import tenant_slug_for
from tests.factories import make_tenant, make_user

CALLER_SUB = "caller01-specs-api"

PROFILE_YAML = """\
spec_version: '0.1'
version: '1.0'
name: test-push-profile
display_name: Pushed profile
description: A profile pushed from a metaseed instance.
root_entity: Study
entities:
  Study:
    description: A study.
    fields:
      - name: identifier
        type: string
        required: true
        is_identifier: true
      - name: title
        type: string
        required: true
"""


def _build_client(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    def _override_user() -> TokenUser:
        return user

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_user] = _override_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def caller(session: AsyncSession) -> TokenUser:
    tenant = make_tenant(name="Caller Tenant", slug=tenant_slug_for(CALLER_SUB))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, email="caller@example.com", keycloak_id=CALLER_SUB))
    await session.commit()
    return TokenUser(sub=CALLER_SUB, email="caller@example.com", name="Caller", roles=[])


async def test_me_names_the_account_and_tenant_the_token_acts_in(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _build_client(session, caller) as client:
        response = await client.get("/api/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "caller@example.com"
    assert body["tenant_name"] == "Caller Tenant"
    assert body["tenant_id"]


async def test_a_pushed_profile_is_a_private_draft_unless_publishing_is_asked_for(
    session: AsyncSession, caller: TokenUser
) -> None:
    """On this hub "published" means visible to every user. A push from a
    metaseed instance must not make that decision on the author's behalf."""
    async with _build_client(session, caller) as client:
        response = await client.post("/api/specs", json={"yaml": PROFILE_YAML})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["visibility"] == "draft"
        assert body["mine"] is True
        assert (body["name"], body["version"]) == ("test-push-profile", "1.0")
        listed = (await client.get("/api/specs")).json()
        assert [(s["name"], s["visibility"]) for s in listed] == [("test-push-profile", "draft")]
        pulled = await client.get("/api/specs/test-push-profile/1.0")
    assert pulled.status_code == 200
    assert "is_identifier: true" in pulled.text
    assert (await session.execute(select(Spec))).scalars().all() == [], "nothing was published"
    draft = (await session.execute(select(SpecDraft))).scalar_one()
    assert draft.name == "test-push-profile"


async def test_pushing_a_revised_profile_updates_the_callers_draft_in_place(
    session: AsyncSession, caller: TokenUser
) -> None:
    changed = PROFILE_YAML.replace("        required: true\n", "        required: false\n", 1)
    async with _build_client(session, caller) as client:
        first = await client.post("/api/specs", json={"yaml": PROFILE_YAML})
        again = await client.post("/api/specs", json={"yaml": PROFILE_YAML})
        revised = await client.post("/api/specs", json={"yaml": changed})
    assert (first.status_code, again.status_code, revised.status_code) == (201, 200, 200)
    assert again.json()["content_hash"] == first.json()["content_hash"]
    assert revised.json()["content_hash"] != first.json()["content_hash"]
    assert len((await session.execute(select(SpecDraft))).scalars().all()) == 1


async def test_publishing_is_explicit_and_lands_for_everyone(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _build_client(session, caller) as client:
        response = await client.post("/api/specs", json={"yaml": PROFILE_YAML, "publish": True})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["visibility"] == "published"
        assert body["content_hash"]
        listed = (await client.get("/api/specs")).json()
    assert [(s["name"], s["visibility"]) for s in listed] == [("test-push-profile", "published")]
    row = (await session.execute(select(Spec))).scalar_one()
    assert row.status == SpecStatus.PUBLISHED
    creator = (await session.execute(select(User).where(User.id == row.created_by_id))).scalar_one()
    assert creator.email == "caller@example.com"


async def test_a_published_version_is_refused_not_replaced(
    session: AsyncSession, caller: TokenUser
) -> None:
    changed = PROFILE_YAML.replace("        required: true\n", "        required: false\n", 1)
    async with _build_client(session, caller) as client:
        first = await client.post("/api/specs", json={"yaml": PROFILE_YAML, "publish": True})
        assert first.status_code == 201
        again = await client.post("/api/specs", json={"yaml": PROFILE_YAML, "publish": True})
        # Identical content is not a second release: the hub already has it.
        assert again.status_code == 200
        replaced = await client.post("/api/specs", json={"yaml": changed, "publish": True})
    assert replaced.status_code == 409
    assert "1.0" in replaced.json()["detail"]
    assert len((await session.execute(select(Spec))).scalars().all()) == 1


async def test_a_published_specification_can_be_withdrawn_to_a_draft(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _build_client(session, caller) as client:
        published = (
            await client.post("/api/specs", json={"yaml": PROFILE_YAML, "publish": True})
        ).json()
        withdrawn = await client.post(f"/api/specs/{published['id']}/unpublish")
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["visibility"] == "draft"
        listed = (await client.get("/api/specs")).json()
    assert [s["visibility"] for s in listed] == ["draft"]
    remaining = (await session.execute(select(Spec).where(Spec.deleted_at.is_(None)))).scalars()
    assert list(remaining) == []


async def test_withdrawing_what_is_not_published_is_a_404(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _build_client(session, caller) as client:
        response = await client.post("/api/specs/00000000-0000-0000-0000-000000000000/unpublish")
    assert response.status_code == 404


async def test_a_pull_of_an_unknown_spec_is_a_404(session: AsyncSession, caller: TokenUser) -> None:
    async with _build_client(session, caller) as client:
        response = await client.get("/api/specs/nothing/1.0")
    assert response.status_code == 404


async def test_a_document_that_is_not_a_profile_is_refused(
    session: AsyncSession, caller: TokenUser
) -> None:
    async with _build_client(session, caller) as client:
        response = await client.post("/api/specs", json={"yaml": "just: text"})
    assert response.status_code == 422
