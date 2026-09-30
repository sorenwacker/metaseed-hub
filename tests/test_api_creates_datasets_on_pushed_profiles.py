"""A dataset created through the API may be built on a pushed profile.

``POST /api/datasets`` -- what ``metaseed hub push-dataset`` calls -- resolved
the profile only among the profiles installed on the server. A profile pushed
to the hub as the caller's draft, or published there, was not found, and the
push was refused with 422 although the metaseed guide promises exactly that
flow: "a user-local profile has to be pushed first". The web picker binds a
draft through ``spec_draft_id`` and the MCP tool resolves a published
specification by name; the API now does both, in that order after the
installed profiles.
"""

from __future__ import annotations

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
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade
from tests.factories import make_spec, make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio

PROFILE = {
    "version": "1.0",
    "name": "pushed_probe",
    "root_entity": "Sample",
    "entities": {
        "Sample": {
            "fields": [
                {"name": "unique_id", "type": "string", "required": True},
                {"name": "title", "type": "string", "required": True},
            ]
        }
    },
}
PAYLOAD = {"entities": [{"_type": "Sample", "unique_id": "S1", "title": "pushed"}]}


async def _caller(session: AsyncSession):
    sub = f"pushed-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(user)
    await session.commit()
    return tenant, user, TokenUser(sub=sub, email=f"{sub}@example.org", name="P", roles=[])


def _api(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create(session: AsyncSession, token: TokenUser, tenant_id: str, name: str):
    async with _api(session, token) as client:
        return await client.post(
            "/api/datasets",
            json={
                "tenant_id": tenant_id,
                "name": name,
                "profile": "pushed_probe",
                "version": "1.0",
                "data": PAYLOAD,
            },
        )


async def _stored(session: AsyncSession, dataset_id: str) -> Dataset:
    return (await session.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one()


async def test_a_dataset_on_the_callers_draft_is_created_and_bound_to_it(
    session: AsyncSession,
) -> None:
    """The push-dataset case: refused with 422 before."""
    tenant, user, token = await _caller(session)
    draft = make_spec_draft(
        tenant=tenant, user=user, name="pushed_probe", version="1.0", spec_data=PROFILE
    )
    session.add(draft)
    await session.commit()

    response = await _create(session, token, tenant.id, "on-a-draft")

    assert response.status_code == 201, response.text
    stored = await _stored(session, response.json()["id"])
    assert stored.spec_draft_id == draft.id and stored.spec_id is None
    state = await ensure_dataset_facade(stored, session)
    assert [n.entity_type for n in state.nodes_by_id.values()] == ["Sample"]


async def test_a_dataset_on_a_published_specification_is_bound_to_it(session: AsyncSession) -> None:
    tenant, user, token = await _caller(session)
    published = make_spec(
        tenant=tenant, created_by=user, name="pushed_probe", version="1.0", spec_data=PROFILE
    )
    session.add(published)
    await session.commit()

    response = await _create(session, token, tenant.id, "on-a-publication")

    assert response.status_code == 201, response.text
    stored = await _stored(session, response.json()["id"])
    assert stored.spec_id == published.id and stored.spec_draft_id is None


async def test_a_publication_wins_over_the_callers_draft_of_the_same_name(
    session: AsyncSession,
) -> None:
    """A release is what other people build on; the draft is private work in progress."""
    tenant, user, token = await _caller(session)
    session.add(
        make_spec_draft(
            tenant=tenant, user=user, name="pushed_probe", version="1.0", spec_data=PROFILE
        )
    )
    published = make_spec(
        tenant=tenant, created_by=user, name="pushed_probe", version="1.0", spec_data=PROFILE
    )
    session.add(published)
    await session.commit()

    response = await _create(session, token, tenant.id, "on-both")

    assert response.status_code == 201, response.text
    assert (await _stored(session, response.json()["id"])).spec_id == published.id


async def test_another_users_draft_is_not_reachable(session: AsyncSession) -> None:
    tenant, user, token = await _caller(session)
    other_tenant, other_user, _ = await _caller(session)
    session.add(
        make_spec_draft(
            tenant=other_tenant,
            user=other_user,
            name="pushed_probe",
            version="1.0",
            spec_data=PROFILE,
        )
    )
    await session.commit()

    response = await _create(session, token, tenant.id, "on-someone-elses-draft")

    assert response.status_code == 422
    assert "pushed_probe" in response.text


async def test_a_profile_the_hub_holds_nowhere_is_still_refused(session: AsyncSession) -> None:
    tenant, _user, token = await _caller(session)

    response = await _create(session, token, tenant.id, "on-nothing")

    assert response.status_code == 422
