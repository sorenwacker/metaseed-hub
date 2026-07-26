"""Account deletion (GDPR right to erasure).

Deleting an account is gated: any dataset the user *solely owns* (owner with no
other owner) would be orphaned by the deletion, so the owner must first reassign
it to a new owner or delete it. The account cannot be removed while such datasets
exist -- the owner decides what happens to each, using the existing dataset
member and delete tools.

Once no dataset is left owner-less, deleting the user row removes all personal
data by cascade (team/dataset/spec memberships, comments, reactions, notes, spec
drafts). Co-owned datasets survive under their remaining owner. Authorship on
shared content that is kept (published specs, dataset versions) is anonymized via
the ``SET NULL`` foreign keys, not deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from metaseed_hub.models import Dataset, DatasetMember, DatasetRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import User


class AccountDeletionBlockedError(Exception):
    """Raised when datasets the user solely owns must be handled first.

    Attributes:
        datasets: The datasets that would be orphaned; each needs a new owner or
            to be deleted before the account can be removed.
    """

    def __init__(self, datasets: list[Dataset]) -> None:
        self.datasets = datasets
        super().__init__(
            f"{len(datasets)} dataset(s) need a new owner or deletion "
            "before the account can be removed"
        )


async def sole_owned_datasets(session: AsyncSession, user: User) -> list[Dataset]:
    """Return datasets the user owns that have no other owner.

    Deleting the user would leave these datasets without an owner, so they block
    account deletion until reassigned or deleted.

    Args:
        session: Database session.
        user: The user whose account is being deleted.

    Returns:
        The blocking datasets (empty if the account can be deleted).
    """
    owned_memberships = (
        (
            await session.execute(
                select(DatasetMember).where(
                    DatasetMember.user_id == user.id,
                    DatasetMember.role == DatasetRole.OWNER,
                )
            )
        )
        .scalars()
        .all()
    )

    blocking: list[Dataset] = []
    for membership in owned_memberships:
        other_owner = (
            (
                await session.execute(
                    select(DatasetMember).where(
                        DatasetMember.dataset_id == membership.dataset_id,
                        DatasetMember.user_id != user.id,
                        DatasetMember.role == DatasetRole.OWNER,
                    )
                )
            )
            .scalars()
            .first()
        )
        if other_owner is None:
            dataset = await session.get(Dataset, membership.dataset_id)
            if dataset is not None:
                blocking.append(dataset)
    return blocking


async def delete_account(session: AsyncSession, user: User) -> None:
    """Delete a user and all their personal data (GDPR erasure).

    Refuses when the user still solely owns any dataset; the caller must surface
    :attr:`AccountDeletionBlockedError.datasets` so the owner can reassign or delete
    them first. On success the user row is deleted, cascading away every personal
    record; the caller is responsible for committing.

    Args:
        session: Database session.
        user: The user to delete.

    Raises:
        AccountDeletionBlockedError: If any dataset would be left without an owner.
    """
    blocking = await sole_owned_datasets(session, user)
    if blocking:
        raise AccountDeletionBlockedError(blocking)

    await session.delete(user)
    await session.flush()
