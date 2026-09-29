"""The one place a dataset's name is written.

A name is unique per tenant (``uq_datasets_tenant_name``), and a soft-deleted
dataset keeps its name. Six paths set one -- the web form, file import,
accession import, the MCP ``create_dataset`` tool, ``POST /api/datasets`` and
the rename in ``PATCH /api/datasets/{id}`` -- and each used to handle the clash
its own way or not at all. The routes caught ``IntegrityError`` around
``commit()``, but ``record_creator`` flushes earlier, so the violation escaped
as a 500; the REST paths caught nothing. Here the constraint is checked at the
flush that writes the row, so two simultaneous requests for one name cannot
both succeed, and every caller receives the same ``DuplicateDatasetNameError``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset
from metaseed_hub.sharing import record_creator, resource_for

NAME_CONSTRAINT = "uq_datasets_tenant_name"


class DuplicateDatasetNameError(Exception):
    """The tenant already has a dataset of this name.

    Attributes:
        name: The name that was refused.
        held_by_deleted: Whether the dataset holding it is soft-deleted, which
            the user no longer sees and so cannot find by looking.
    """

    def __init__(self, name: str, held_by_deleted: bool) -> None:
        self.name = name
        self.held_by_deleted = held_by_deleted
        if held_by_deleted:
            message = f"The name {name!r} is held by a deleted dataset; choose a different name"
        else:
            message = f"A dataset named {name!r} already exists; choose a different name"
        super().__init__(message)


async def create_dataset(
    session: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    profile: str,
    version: str,
    creator_id: str | None,
    spec_draft_id: str | None = None,
    spec_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> Dataset:
    """Insert a dataset and make its creator the owner, without committing.

    Args:
        session: Database session; the caller commits.
        tenant_id: Tenant the dataset belongs to.
        name: Dataset name, unique within the tenant.
        profile: Profile name.
        version: Profile version.
        creator_id: Database id of the creating user, or None for a caller that
            acts without a person.
        spec_draft_id: Draft specification the dataset is built on, if any.
        spec_id: Published specification the dataset is built on, if any.
        data: Initial stored contents; empty when omitted.

    Returns:
        The flushed dataset, with its id assigned.

    Raises:
        DuplicateDatasetNameError: If the tenant already has a dataset of that name.
            The session has been rolled back.
    """
    dataset = Dataset(
        tenant_id=tenant_id,
        name=name,
        profile=profile,
        version=version,
        spec_draft_id=spec_draft_id,
        spec_id=spec_id,
        data=data if data is not None else {},
    )
    session.add(dataset)
    await _flush_or_refuse(session, tenant_id, name)
    await record_creator(session, resource_for("dataset"), dataset, creator_id)
    return dataset


async def rename_dataset(session: AsyncSession, dataset: Dataset, name: str) -> None:
    """Give a dataset a new name, without committing.

    Args:
        session: Database session; the caller commits.
        dataset: The dataset to rename.
        name: The new name, unique within the dataset's tenant.

    Raises:
        DuplicateDatasetNameError: If another dataset in the tenant has that name.
            The session has been rolled back.
    """
    dataset.name = name
    await _flush_or_refuse(session, dataset.tenant_id, name)


async def _flush_or_refuse(session: AsyncSession, tenant_id: str, name: str) -> None:
    """Flush, turning a name clash into ``DuplicateDatasetNameError``.

    Any other integrity violation is not a naming problem and propagates.
    """
    try:
        await session.flush()
    except IntegrityError as exc:
        if NAME_CONSTRAINT not in str(exc.orig):
            raise
        await session.rollback()
        holder = (
            await session.execute(
                select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.name == name)
            )
        ).scalar_one_or_none()
        raise DuplicateDatasetNameError(
            name, held_by_deleted=bool(holder and holder.is_deleted)
        ) from exc
