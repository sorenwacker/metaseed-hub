"""Tests for the dataset REST API tenant scoping and soft-delete behavior.

These exercise the mounted ``/api/datasets`` router with the database session
and the authenticated user dependency overridden, verifying that:

- queries are scoped to the caller's tenant (no cross-tenant access),
- soft-deleted rows are never returned, and
- delete performs a soft delete rather than removing the row.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.api import api_router
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset, Tenant
from metaseed_hub.ui.dependencies import tenant_slug_for
from tests.factories import make_dataset, make_tenant, make_user

CALLER_SUB = "caller01-rest-api"


def _build_client(session: AsyncSession, user: TokenUser) -> AsyncClient:
    """Build an httpx client for the API router with deps overridden."""
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
    """An authenticated user whose tenant slug is derived from ``CALLER_SUB``."""
    return TokenUser(
        sub=CALLER_SUB,
        email="caller@example.com",
        name="Caller",
        roles=[],
    )


@pytest_asyncio.fixture
async def own_tenant_id(session: AsyncSession) -> str:
    """Persist the caller's tenant (slug derived from CALLER_SUB) and return its id."""
    # The account and its person, as sign-in provisions them: access is read
    # from who the caller is, so a tenant with nobody in it reaches nothing.
    tenant = make_tenant(name="Caller Tenant", slug=tenant_slug_for(CALLER_SUB))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=CALLER_SUB, email="caller@example.com"))
    await session.commit()
    return tenant.id


@pytest_asyncio.fixture
async def other_tenant_id(session: AsyncSession) -> str:
    """Persist a second tenant that the caller must not be able to reach."""
    tenant = make_tenant(name="Other Tenant", slug="other999")
    session.add(tenant)
    await session.commit()
    return tenant.id


async def _add_dataset(session: AsyncSession, tenant_id: str, name: str) -> Dataset:
    tenant = await session.get(Tenant, tenant_id)
    assert tenant is not None
    dataset = make_dataset(tenant=tenant, name=name)
    session.add(dataset)
    await session.commit()
    return dataset


@pytest.mark.asyncio
async def test_list_scoped_to_own_tenant_excludes_other_tenants(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str, other_tenant_id: str
) -> None:
    """list_datasets returns only the caller tenant's rows."""
    await _add_dataset(session, own_tenant_id, "mine-1")
    await _add_dataset(session, own_tenant_id, "mine-2")
    await _add_dataset(session, other_tenant_id, "theirs")

    async with _build_client(session, caller) as client:
        resp = await client.get("/api/datasets", params={"tenant_id": own_tenant_id})

    assert resp.status_code == 200
    names = {d["name"] for d in resp.json()}
    assert names == {"mine-1", "mine-2"}


@pytest.mark.asyncio
async def test_list_rejects_other_tenant_id(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str, other_tenant_id: str
) -> None:
    """Requesting another tenant's datasets is denied."""
    async with _build_client(session, caller) as client:
        resp = await client.get("/api/datasets", params={"tenant_id": other_tenant_id})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_excludes_soft_deleted(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """Soft-deleted datasets do not appear in the listing."""
    keep = await _add_dataset(session, own_tenant_id, "keep")
    gone = await _add_dataset(session, own_tenant_id, "gone")
    gone.soft_delete()
    await session.commit()

    async with _build_client(session, caller) as client:
        resp = await client.get("/api/datasets", params={"tenant_id": own_tenant_id})

    assert resp.status_code == 200
    assert {d["name"] for d in resp.json()} == {"keep"}
    assert keep.id  # keep referenced


@pytest.mark.asyncio
async def test_get_other_tenant_dataset_returns_404(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str, other_tenant_id: str
) -> None:
    """Fetching a dataset owned by another tenant returns 404, not the row."""
    other = await _add_dataset(session, other_tenant_id, "secret")

    async with _build_client(session, caller) as client:
        resp = await client.get(f"/api/datasets/{other.id}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_soft_deleted_returns_404(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """A soft-deleted dataset is not retrievable."""
    ds = await _add_dataset(session, own_tenant_id, "deleted")
    ds.soft_delete()
    await session.commit()

    async with _build_client(session, caller) as client:
        resp = await client.get(f"/api/datasets/{ds.id}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_other_tenant_dataset_returns_404(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str, other_tenant_id: str
) -> None:
    """Updating another tenant's dataset is not possible."""
    other = await _add_dataset(session, other_tenant_id, "secret")

    async with _build_client(session, caller) as client:
        resp = await client.patch(f"/api/datasets/{other.id}", json={"name": "hijacked"})

    assert resp.status_code == 404
    await session.refresh(other)
    assert other.name == "secret"


@pytest.mark.asyncio
async def test_delete_performs_soft_delete(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """Delete marks the row deleted instead of removing it."""
    ds = await _add_dataset(session, own_tenant_id, "to-delete")
    ds_id = ds.id

    async with _build_client(session, caller) as client:
        resp = await client.delete(f"/api/datasets/{ds_id}")

    assert resp.status_code == 204

    # Row still exists but is marked deleted.
    row = (await session.execute(select(Dataset).where(Dataset.id == ds_id))).scalar_one_or_none()
    assert row is not None
    assert row.deleted_at is not None

    # And it is no longer retrievable through the API.
    async with _build_client(session, caller) as client:
        resp = await client.get(f"/api/datasets/{ds_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_other_tenant_dataset_returns_404(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str, other_tenant_id: str
) -> None:
    """Delete cannot reach another tenant's dataset."""
    other = await _add_dataset(session, other_tenant_id, "secret")

    async with _build_client(session, caller) as client:
        resp = await client.delete(f"/api/datasets/{other.id}")

    assert resp.status_code == 404
    await session.refresh(other)
    assert other.deleted_at is None


@pytest.mark.asyncio
async def test_create_rejects_other_tenant(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str, other_tenant_id: str
) -> None:
    """Creating a dataset under another tenant is denied."""
    payload = {
        "tenant_id": other_tenant_id,
        "name": "intruder",
        "profile": "miappe",
        "version": "1.1",
    }
    async with _build_client(session, caller) as client:
        resp = await client.post("/api/datasets", json=payload)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_in_own_tenant_succeeds(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """Creating a dataset under the caller's own tenant works."""
    payload = {
        "tenant_id": own_tenant_id,
        "name": "legit",
        "profile": "miappe",
        "version": "1.1",
    }
    async with _build_client(session, caller) as client:
        resp = await client.post("/api/datasets", json=payload)

    assert resp.status_code == 201
    assert resp.json()["name"] == "legit"


@pytest.mark.asyncio
async def test_create_rejects_invalid_name(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """The REST create path enforces the same name rule as the repository save."""
    async with _build_client(session, caller) as client:
        resp = await client.post(
            "/api/datasets",
            json={
                "tenant_id": own_tenant_id,
                "name": "bad/name",
                "profile": "miappe",
                "version": "1.2",
            },
        )
    assert resp.status_code == 422
    assert "alphanumeric" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_accepts_valid_name(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """A valid name creates the dataset."""
    async with _build_client(session, caller) as client:
        resp = await client.post(
            "/api/datasets",
            json={
                "tenant_id": own_tenant_id,
                "name": "good-name",
                "profile": "miappe",
                "version": "1.2",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["name"] == "good-name"


@pytest.mark.asyncio
async def test_update_rejects_invalid_name(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """PATCH enforces the dataset name rule too."""
    ds = await _add_dataset(session, own_tenant_id, "start-name")
    async with _build_client(session, caller) as client:
        resp = await client.patch(f"/api/datasets/{ds.id}", json={"name": "bad/name"})
    assert resp.status_code == 422
    assert "alphanumeric" in resp.json()["detail"]


async def test_create_refuses_a_payload_its_profile_cannot_load(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """A pushed dataset built on a profile this hub does not have used to be
    stored as-is and could then not be opened; now it is refused, as a PATCH
    with the same payload already was."""
    async with _build_client(session, caller) as client:
        response = await client.post(
            "/api/datasets",
            json={
                "tenant_id": own_tenant_id,
                "name": "test-unknown-profile",
                "profile": "no-such-profile",
                "version": "9.9",
                "data": {"entities": [{"_type": "Thing", "identifier": "x"}]},
            },
        )
    assert response.status_code in (409, 422), response.text
    assert (await session.execute(select(Dataset))).scalars().all() == []


async def test_validation_does_not_rewrite_the_stored_payload(
    session: AsyncSession, caller: TokenUser, own_tenant_id: str
) -> None:
    """Loading the payload into a facade replaces nested dicts (an ISA ontology
    source, say) with model objects in place; storing those as JSONB failed
    with 500 on the first real dataset pushed from metaseed. Validation must
    work on its own copy and the row must hold plain JSON."""
    # The flat form metaseed pushes: the ontology source is its own entity,
    # linked to the investigation by its parent id; the load nests it back.
    payload = {
        "entities": [
            {"_type": "Investigation", "identifier": "I1", "title": "An investigation"},
            {
                "_type": "OntologySource",
                "name": "OBI",
                "file": "http://purl.obolibrary.org/obo/obi.owl",
                "version": "1",
                "_parent_unique_id": "I1",
            },
        ]
    }
    async with _build_client(session, caller) as client:
        response = await client.post(
            "/api/datasets",
            json={
                "tenant_id": own_tenant_id,
                "name": "test-with-ontology-source",
                "profile": "isa",
                "version": "1.0",
                "data": payload,
            },
        )
        assert response.status_code == 201, response.text
        patched = await client.patch(
            f"/api/datasets/{response.json()['id']}", json={"data": payload}
        )
    assert patched.status_code == 200, patched.text
    row = (await session.execute(select(Dataset))).scalar_one()
    assert row.data == payload


@pytest.mark.asyncio
async def test_create_makes_the_caller_the_owner(session, caller, own_tenant_id) -> None:
    """The creator holds the owner role, so they can share what they made."""
    from metaseed_hub.sharing import Role, resource_for, role_of
    from metaseed_hub.ui.dependencies import ensure_tenant_and_user

    _tenant, user = await ensure_tenant_and_user(session, caller)
    async with _build_client(session, caller) as client:
        resp = await client.post(
            "/api/datasets",
            json={
                "tenant_id": own_tenant_id,
                "name": "made-by-me",
                "profile": "miappe",
                "version": "1.1",
                "data": {},
            },
        )
    assert resp.status_code == 201, resp.text
    role = await role_of(session, resource_for("dataset"), resp.json()["id"], user.id)
    assert role is Role.OWNER
