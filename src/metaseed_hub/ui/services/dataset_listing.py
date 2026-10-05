"""Which datasets a person can open.

Its own module because two pages list them — the dataset list and the SEEK
page — and the second copying the first's queries would be two rules to keep
in step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from metaseed_hub.models import Dataset
from metaseed_hub.sharing import accessible_ids, resource_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def datasets_visible_to(
    session: AsyncSession, tenant_id: str, user_id: str
) -> tuple[list[Dataset], set[str]]:
    """Every dataset the person owns or was given access to, newest first.

    Args:
        session: Database session.
        tenant_id: The person's own tenant, whose datasets they own.
        user_id: The person, for memberships and collaboration grants.

    Returns:
        The datasets, owned ones first and each once, and the ids of the owned
        ones.
    """
    owned = list(
        (
            await session.execute(
                select(Dataset)
                .where(Dataset.tenant_id == tenant_id, Dataset.deleted_at.is_(None))
                .order_by(Dataset.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    shared = list(
        (
            await session.execute(
                select(Dataset)
                .where(
                    Dataset.id.in_(await accessible_ids(session, resource_for("dataset"), user_id)),
                    Dataset.deleted_at.is_(None),
                )
                .order_by(Dataset.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    owned_ids = {ds.id for ds in owned}
    return owned + [ds for ds in shared if ds.id not in owned_ids], owned_ids
