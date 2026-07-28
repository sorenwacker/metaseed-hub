"""An administrator removing a dataset or specification in any workspace.

An admin acts on someone else's data here, so the failures that matter are
acting on the wrong item and acting irreversibly. Every assertion is about what
the owner can see afterwards, and about the description handed back — which is
the only thing standing between a mistyped identifier and the wrong person's
work disappearing.
"""

from __future__ import annotations

import pytest
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset, Spec, SpecStatus
from metaseed_hub.ui.routes.admin import RemovalError, set_removed
from tests.factories import make_dataset, make_spec, make_tenant, make_user


def _spec_data() -> dict:
    return {"spec": ProfileSpec(name="T", version="1.0").model_dump(mode="json")}


async def _workspace(session: AsyncSession, *, slug: str, email: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant, email=email)
    session.add(owner)
    await session.flush()
    return tenant, owner


async def _owner_sees_datasets(session: AsyncSession, tenant_id: str) -> list[Dataset]:
    """Datasets as the owner's workspace lists them."""
    result = await session.execute(
        select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.deleted_at.is_(None))
    )
    return list(result.scalars().all())


async def _owner_sees_specs(session: AsyncSession, tenant_id: str) -> list[Spec]:
    result = await session.execute(
        select(Spec).where(
            Spec.tenant_id == tenant_id,
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
        )
    )
    return list(result.scalars().all())


async def test_a_removed_dataset_leaves_its_owners_workspace(session: AsyncSession) -> None:
    tenant, owner = await _workspace(session, slug="own00001", email="owner@example.org")
    dataset = make_dataset(tenant=tenant, name="oops")
    session.add(dataset)
    await session.commit()

    await set_removed(session, "dataset", dataset.id, removed=True)

    assert await _owner_sees_datasets(session, tenant.id) == []


async def test_a_removed_spec_leaves_its_owners_workspace(session: AsyncSession) -> None:
    tenant, owner = await _workspace(session, slug="own00002", email="owner2@example.org")
    spec = make_spec(tenant=tenant, created_by=owner, spec_data=_spec_data())
    session.add(spec)
    await session.commit()

    await set_removed(session, "spec", spec.id, removed=True)

    assert await _owner_sees_specs(session, tenant.id) == []


async def test_the_description_names_the_item_and_its_owner(session: AsyncSession) -> None:
    """The only guard against a mistyped identifier removing the wrong thing."""
    tenant, _owner = await _workspace(session, slug="own00003", email="alice@example.org")
    dataset = make_dataset(tenant=tenant, name="quarterly-yields")
    session.add(dataset)
    await session.commit()

    message = await set_removed(session, "dataset", dataset.id, removed=True)

    assert "quarterly-yields" in message
    assert "alice@example.org" in message


async def test_removal_is_reversible(session: AsyncSession) -> None:
    """Soft, because an admin acting irreversibly on a guessed id is not safe."""
    tenant, _owner = await _workspace(session, slug="own00004", email="bob@example.org")
    dataset = make_dataset(tenant=tenant, name="restore-me")
    session.add(dataset)
    await session.commit()
    await set_removed(session, "dataset", dataset.id, removed=True)

    message = await set_removed(session, "dataset", dataset.id, removed=False)

    assert "Restored" in message
    assert len(await _owner_sees_datasets(session, tenant.id)) == 1


async def test_nothing_is_erased(session: AsyncSession) -> None:
    tenant, _owner = await _workspace(session, slug="own00005", email="c@example.org")
    dataset = make_dataset(tenant=tenant, name="kept")
    session.add(dataset)
    await session.commit()

    await set_removed(session, "dataset", dataset.id, removed=True)

    still_there = await session.get(Dataset, dataset.id)
    assert still_there is not None
    assert still_there.deleted_at is not None


async def test_an_unknown_id_changes_nothing(session: AsyncSession) -> None:
    tenant, _owner = await _workspace(session, slug="own00006", email="d@example.org")
    dataset = make_dataset(tenant=tenant, name="untouched")
    session.add(dataset)
    await session.commit()

    with pytest.raises(RemovalError, match="No dataset with id"):
        await set_removed(session, "dataset", "00000000-0000-0000-0000-000000000000", removed=True)

    assert len(await _owner_sees_datasets(session, tenant.id)) == 1


async def test_an_unknown_kind_is_refused(session: AsyncSession) -> None:
    """The kind comes from a form field, so it must not reach getattr-style
    lookup of arbitrary models."""
    with pytest.raises(RemovalError, match="Unknown kind"):
        await set_removed(session, "user", "whatever", removed=True)


async def test_removing_twice_is_refused(session: AsyncSession) -> None:
    """Reports the truth rather than silently succeeding, so an admin who pasted
    a stale identifier learns it."""
    tenant, _owner = await _workspace(session, slug="own00007", email="e@example.org")
    dataset = make_dataset(tenant=tenant, name="once")
    session.add(dataset)
    await session.commit()
    await set_removed(session, "dataset", dataset.id, removed=True)

    with pytest.raises(RemovalError, match="already removed"):
        await set_removed(session, "dataset", dataset.id, removed=True)


async def test_removal_does_not_touch_another_workspace(session: AsyncSession) -> None:
    tenant_a, _a = await _workspace(session, slug="own00008", email="f@example.org")
    tenant_b, _b = await _workspace(session, slug="own00009", email="g@example.org")
    a = make_dataset(tenant=tenant_a, name="target")
    b = make_dataset(tenant=tenant_b, name="bystander")
    session.add_all([a, b])
    await session.commit()

    await set_removed(session, "dataset", a.id, removed=True)

    assert await _owner_sees_datasets(session, tenant_a.id) == []
    assert len(await _owner_sees_datasets(session, tenant_b.id)) == 1
