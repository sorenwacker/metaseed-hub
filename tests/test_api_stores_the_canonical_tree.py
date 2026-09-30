"""A dataset written through the API is stored the way every other dataset is.

The hub stores one form: the tree `serialize_tree` produces, stamped with the
specification hash. `POST` and `PATCH /api/datasets` stored the payload as
sent instead, so the eight CropXR examples pushed from a metaseed instance
held a flat `entities` list: the home cards, which count the tree, said
"No entities", and the drift check had no provenance to compare.
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
from metaseed_hub.ui.helpers.tree import count_entities_by_type
from tests.factories import make_tenant, make_user

pytestmark = pytest.mark.asyncio

FLAT = {
    "entities": [
        {"_type": "Investigation", "unique_id": "INV-1", "title": "T"},
        {
            "_type": "Study",
            "unique_id": "STU-1",
            "investigation_id": "INV-1",
            "title": "S",
            "_parent_unique_id": "INV-1",
        },
    ]
}


async def _caller(session: AsyncSession) -> tuple[str, TokenUser]:
    sub = f"canon-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org"))
    await session.commit()
    return tenant.id, TokenUser(sub=sub, email=f"{sub}@example.org", name="C", roles=[])


def _api(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _row(session: AsyncSession, dataset_id: str) -> Dataset:
    return (await session.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one()


async def test_a_created_dataset_is_stored_as_a_stamped_tree(session: AsyncSession) -> None:
    """The push-dataset case: the card said "No entities"."""
    tenant_id, user = await _caller(session)

    async with _api(session, user) as client:
        response = await client.post(
            "/api/datasets",
            json={
                "tenant_id": tenant_id,
                "name": "pushed",
                "profile": "miappe",
                "version": "1.2",
                "data": FLAT,
            },
        )

    assert response.status_code == 201, response.text
    row = await _row(session, response.json()["id"])
    assert "entities" not in row.data and "tree" in row.data
    assert sum(count_entities_by_type(row.data["tree"]).values()) == 2, "what the home card counts"
    assert row.data["spec_hash"].startswith("sha256:")


async def test_a_replaced_dataset_is_stored_as_a_stamped_tree(session: AsyncSession) -> None:
    tenant_id, user = await _caller(session)

    async with _api(session, user) as client:
        created = await client.post(
            "/api/datasets",
            json={
                "tenant_id": tenant_id,
                "name": "replaced",
                "profile": "miappe",
                "version": "1.2",
            },
        )
        patched = await client.patch(f"/api/datasets/{created.json()['id']}", json={"data": FLAT})

    assert patched.status_code == 200, patched.text
    row = await _row(session, created.json()["id"])
    assert sum(count_entities_by_type(row.data.get("tree", [])).values()) == 2
    assert row.data.get("spec_hash", "").startswith("sha256:")
