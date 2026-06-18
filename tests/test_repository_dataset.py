"""Tests for DatabaseDatasetRepository save semantics.

Covers two correctness issues:

- save() must not mutate the caller's DatasetData.entities, and
- a dataset name must be reusable after the previous dataset of that name was
  soft-deleted (the unique constraint is not scoped to deleted_at).
"""

import pytest
from metaseed.repositories import DatasetData
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset
from metaseed_hub.repositories.dataset import DatabaseDatasetRepository
from tests.factories import make_tenant


async def _repo(session: AsyncSession) -> DatabaseDatasetRepository:
    tenant = make_tenant()
    session.add(tenant)
    await session.commit()
    return DatabaseDatasetRepository(session, tenant.id)


@pytest.mark.asyncio
async def test_list_orders_by_modified_most_recent_first(session: AsyncSession) -> None:
    """list() returns datasets ordered by most-recently-updated first."""
    repo = await _repo(session)

    def _data(name: str, profile: str = "miappe", version: str = "1.1") -> DatasetData:
        return DatasetData(name=name, profile=profile, version=version, entities=[])

    await repo.save("alpha", _data("alpha"))
    await repo.save("beta", _data("beta"))
    # Touch "alpha" so it becomes the most recently updated.
    await repo.save("alpha", _data("alpha", profile="isa", version="1.0"))

    listing = await repo.list()

    names = [info.name for info in listing]
    assert names[0] == "alpha"
    assert set(names) == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_save_does_not_mutate_caller_entities(session: AsyncSession) -> None:
    """save() leaves the caller's entity dicts (including _type) intact."""
    repo = await _repo(session)
    entities = [
        {"_type": "Investigation", "_parent_unique_id": None, "unique_id": "inv1", "name": "X"},
        {"_type": "Study", "_parent_unique_id": "inv1", "unique_id": "st1", "name": "Y"},
    ]
    data = DatasetData(name="d1", profile="miappe", version="1.1", entities=entities)

    await repo.save("d1", data)

    # The caller's structural keys must survive the save unchanged.
    assert entities[0]["_type"] == "Investigation"
    assert entities[0]["_parent_unique_id"] is None
    assert entities[1]["_type"] == "Study"
    assert entities[1]["_parent_unique_id"] == "inv1"


@pytest.mark.asyncio
async def test_save_after_soft_delete_reuses_name(session: AsyncSession) -> None:
    """A name can be saved again after the prior dataset was soft-deleted."""
    repo = await _repo(session)
    await repo.save("d1", DatasetData(name="d1", profile="miappe", version="1.1", entities=[]))

    assert await repo.delete("d1") is True
    assert await repo.exists("d1") is False

    # Saving the same name again must succeed (restore/overwrite, not collide).
    info = await repo.save("d1", DatasetData(name="d1", profile="isa", version="1.0", entities=[]))
    assert info.name == "d1"
    assert info.profile == "isa"
    assert await repo.exists("d1") is True

    # And there is still exactly one row for that (tenant, name).
    rows = (await session.execute(select(Dataset).where(Dataset.name == "d1"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].deleted_at is None
