"""Account deletion (GDPR right to erasure).

Deleting an account is gated: any dataset the user *solely owns* (owner with no
other owner) would be orphaned by the deletion, so the owner must first reassign
it to a new owner or delete it. The account cannot be removed while such datasets
exist -- the owner decides what happens to each, using the existing dataset
member and delete tools.

A dataset the user already soft-deleted, or a spec they withdrew, does not
block: every list view filters those out, so a blocker among them would be one
the user is told to reassign but cannot see or act on — with soft delete as the
only delete they have, their account would be unremovable. Not blocking is not
the same as surviving: those rows are hard-deleted during erasure, since
nothing else in the codebase ever removes a Dataset or Spec and leaving them
would keep the user's stored data after their account was erased. Rows a second
owner shares are exempt and stay restorable.

Once no dataset is left owner-less, deleting the user row removes their
personal records by cascade (team/dataset/spec memberships, comments,
reactions, notes, spec drafts). Two things do not hang off the user row and are
erased explicitly: the ``SeekConnection`` (keyed by tenant, holding the user's
encrypted SEEK API key) and, where nothing else lives in it, the per-user
tenant itself, whose name and slug are derived from the person. A tenant still
holding datasets or specs that survive the user is scrubbed to an opaque name
instead — erasing the person must not destroy the co-owned work that outlives
them. Authorship on shared content that is kept (published specs, dataset
versions) is anonymized via the ``SET NULL`` foreign keys, not deleted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from metaseed_hub.models import (
    Dataset,
    DatasetMember,
    Role,
    Spec,
    SpecMember,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import User


class AccountDeletionBlockedError(Exception):
    """Raised when datasets or specs the user owns must be handled first.

    Attributes:
        datasets: Datasets that would be left without an owner; each needs a new
            owner or to be deleted before the account can be removed.
        specs: Specs that would be left without an owner; handled the same way.
    """

    def __init__(self, datasets: list[Dataset], specs: list[Spec]) -> None:
        self.datasets = datasets
        self.specs = specs
        super().__init__(
            f"{len(datasets) + len(specs)} item(s) need a new owner or deletion "
            "before the account can be removed"
        )


async def datasets_needing_new_owner(session: AsyncSession, user: User) -> list[Dataset]:
    """Return datasets that would be left without an owner if the user were removed.

    A dataset blocks account deletion whenever removing the user leaves it with no
    ``OWNER`` member -- whether the user is its sole owner, or its only member at
    all (any role). Such a dataset must first be reassigned to a new owner or
    deleted.

    Args:
        session: Database session.
        user: The user whose account is being deleted.

    Returns:
        The blocking datasets (empty if the account can be deleted).
    """
    memberships = (
        (await session.execute(select(DatasetMember).where(DatasetMember.user_id == user.id)))
        .scalars()
        .all()
    )

    blocking: list[Dataset] = []
    for membership in memberships:
        remaining_owner = (
            (
                await session.execute(
                    select(DatasetMember).where(
                        DatasetMember.dataset_id == membership.dataset_id,
                        DatasetMember.user_id != user.id,
                        DatasetMember.role == Role.OWNER,
                    )
                )
            )
            .scalars()
            .first()
        )
        if remaining_owner is None:
            dataset = await session.get(Dataset, membership.dataset_id)
            # A dataset the user already deleted cannot need a new owner; the
            # UI hides it, so as a blocker it would be unresolvable.
            if dataset is not None and dataset.deleted_at is None:
                blocking.append(dataset)
    return blocking


async def specs_needing_new_owner(session: AsyncSession, user: User) -> list[Spec]:
    """Return specs that would be left without an owner if the user were removed.

    The spec counterpart of :func:`datasets_needing_new_owner`: a spec blocks
    account deletion when no ``OWNER`` member would remain after the user is
    removed.

    Args:
        session: Database session.
        user: The user whose account is being deleted.

    Returns:
        The blocking specs (empty if none).
    """
    memberships = (
        (await session.execute(select(SpecMember).where(SpecMember.user_id == user.id)))
        .scalars()
        .all()
    )

    blocking: list[Spec] = []
    for membership in memberships:
        remaining_owner = (
            (
                await session.execute(
                    select(SpecMember).where(
                        SpecMember.spec_id == membership.spec_id,
                        SpecMember.user_id != user.id,
                        SpecMember.role == Role.OWNER,
                    )
                )
            )
            .scalars()
            .first()
        )
        if remaining_owner is None:
            spec = await session.get(Spec, membership.spec_id)
            # Withdrawn (soft-deleted) specs are invisible everywhere else.
            if spec is not None and spec.deleted_at is None:
                blocking.append(spec)
    return blocking


async def delete_account(session: AsyncSession, user: User) -> None:
    """Delete a user and all their personal data (GDPR erasure).

    Refuses when deleting the user would leave any dataset or spec without an
    owner; the caller must surface :attr:`AccountDeletionBlockedError.datasets`
    and :attr:`~AccountDeletionBlockedError.specs` so the owner can reassign or
    delete them first. On success the user row is deleted, cascading away every
    personal record; the caller is responsible for committing.

    Args:
        session: Database session.
        user: The user to delete.

    Raises:
        AccountDeletionBlockedError: If any dataset or spec would be left without
            an owner.
    """
    datasets = await datasets_needing_new_owner(session, user)
    specs = await specs_needing_new_owner(session, user)
    if datasets or specs:
        raise AccountDeletionBlockedError(datasets, specs)

    tenant_id = user.tenant_id
    await _erase_solely_owned_tombstones(session, user)
    await session.delete(user)
    await session.flush()
    await _erase_tenant_footprint(session, tenant_id)


async def _erase_solely_owned_tombstones(session: AsyncSession, user: User) -> None:
    """Hard-delete the user's own soft-deleted datasets and specs.

    These are deliberately not blockers: every list view hides them, so asking
    the owner to reassign one they cannot see would make deletion impossible.
    But nothing else ever hard-deletes a Dataset or Spec, so leaving them meant
    a user's erased account kept its full stored data — invisible, unowned, and
    not erased at all.

    Rows another owner shares are left alone: erasing the person must not
    destroy work that outlives them, and a co-owner can still restore a
    soft-deleted dataset.

    Args:
        session: Database session.
        user: The user being erased.
    """
    doomed_datasets: list[str] = []
    doomed_specs: list[str] = []

    dataset_memberships = (
        (
            await session.execute(
                select(DatasetMember).where(
                    DatasetMember.user_id == user.id,
                    DatasetMember.role == Role.OWNER,
                )
            )
        )
        .scalars()
        .all()
    )
    for membership in dataset_memberships:
        other_owner = (
            (
                await session.execute(
                    select(DatasetMember).where(
                        DatasetMember.dataset_id == membership.dataset_id,
                        DatasetMember.user_id != user.id,
                        DatasetMember.role == Role.OWNER,
                    )
                )
            )
            .scalars()
            .first()
        )
        if other_owner is not None:
            continue
        doomed_datasets.append(membership.dataset_id)

    spec_memberships = (
        (
            await session.execute(
                select(SpecMember).where(
                    SpecMember.user_id == user.id,
                    SpecMember.role == Role.OWNER,
                )
            )
        )
        .scalars()
        .all()
    )
    for spec_membership in spec_memberships:
        other_spec_owner = (
            (
                await session.execute(
                    select(SpecMember).where(
                        SpecMember.spec_id == spec_membership.spec_id,
                        SpecMember.user_id != user.id,
                        SpecMember.role == Role.OWNER,
                    )
                )
            )
            .scalars()
            .first()
        )
        if other_spec_owner is not None:
            continue
        doomed_specs.append(spec_membership.spec_id)

    # Deleted as statements rather than ORM instances: the membership rows are
    # loaded in this session, and the ORM would try to blank their primary-key
    # foreign keys instead of letting the database's ON DELETE CASCADE run.
    if doomed_datasets:
        await session.execute(
            delete(Dataset).where(Dataset.id.in_(doomed_datasets), Dataset.deleted_at.is_not(None))
        )
    if doomed_specs:
        await session.execute(
            delete(Spec).where(Spec.id.in_(doomed_specs), Spec.deleted_at.is_not(None))
        )
    await session.flush()


async def _erase_tenant_footprint(session: AsyncSession, tenant_id: str) -> None:
    """Remove the personal data that does not hang off the user row.

    Hub tenants are per user and named after them, and the ``SeekConnection``
    is keyed by tenant while holding the user's encrypted SEEK API key — the
    cascade from the user row reaches neither. The connection is always
    deleted. The tenant is deleted when nothing else lives in it; when
    surviving co-owned datasets or specs do, its name and slug are scrubbed to
    an opaque value instead, because erasing the person must not destroy the
    work that outlives them.
    """
    from metaseed_hub.models import SeekConnection, Tenant

    for connection in (
        (await session.execute(select(SeekConnection).where(SeekConnection.tenant_id == tenant_id)))
        .scalars()
        .all()
    ):
        await session.delete(connection)

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        return
    # Every remaining row counts, soft-deleted or not: this runs after the
    # user's own tombstones are erased, so what is left is work that genuinely
    # survives them. A co-owned dataset the user had soft-deleted still lives
    # here and still references the tenant — dropping it would violate the
    # dataset's NOT NULL tenant_id and take a restorable dataset with it.
    has_dataset = (
        (await session.execute(select(Dataset.id).where(Dataset.tenant_id == tenant_id).limit(1)))
        .scalars()
        .first()
    )
    has_spec = (
        (await session.execute(select(Spec.id).where(Spec.tenant_id == tenant_id).limit(1)))
        .scalars()
        .first()
    )
    if has_dataset is None and has_spec is None:
        await session.delete(tenant)
    else:
        opaque = f"deleted-{tenant.id[:8]}"
        tenant.name = opaque
        tenant.slug = opaque
    await session.flush()
