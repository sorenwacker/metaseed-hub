"""Tests for account deletion (GDPR right to erasure).

Deletion is gated: a dataset the user solely owns must be reassigned or deleted
first. Once no dataset is left owner-less, deleting the user cascades away all
personal data while co-owned datasets survive under their remaining owner.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from metaseed_hub.models import (
    Comment,
    Dataset,
    DatasetMember,
    DatasetRole,
    User,
)
from metaseed_hub.repositories.account import (
    AccountDeletionBlockedError,
    datasets_needing_new_owner,
    delete_account,
)
from tests.factories import make_dataset, make_note, make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _add(session, obj):
    """Persist one object and flush so its generated id is available."""
    session.add(obj)
    await session.flush()
    return obj


async def _own(session, user, dataset, role=DatasetRole.OWNER):
    member = DatasetMember(dataset_id=dataset.id, user_id=user.id, role=role)
    session.add(member)
    await session.flush()
    return member


async def test_sole_owned_dataset_blocks_deletion(session):
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, user, dataset)

    blocking = await datasets_needing_new_owner(session, user)
    assert [d.id for d in blocking] == [dataset.id]

    with pytest.raises(AccountDeletionBlockedError) as exc:
        await delete_account(session, user)
    assert [d.id for d in exc.value.datasets] == [dataset.id]

    # user still present -- nothing was deleted
    assert await session.get(User, user.id) is not None


async def test_co_owned_dataset_allows_deletion_and_survives(session):
    tenant = await _add(session, make_tenant())
    leaving = await _add(session, make_user(tenant=tenant, display_name="Leaving"))
    staying = await _add(session, make_user(tenant=tenant, display_name="Staying"))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, leaving, dataset)
    await _own(session, staying, dataset)

    assert await datasets_needing_new_owner(session, leaving) == []
    await delete_account(session, leaving)
    await session.commit()

    assert await session.get(User, leaving.id) is None
    # dataset survives under the remaining owner; leaving user's membership gone
    assert await session.get(Dataset, dataset.id) is not None
    members = (
        (await session.execute(select(DatasetMember).where(DatasetMember.dataset_id == dataset.id)))
        .scalars()
        .all()
    )
    assert [m.user_id for m in members] == [staying.id]
    assert members[0].role == DatasetRole.OWNER


async def test_viewer_membership_does_not_block(session):
    tenant = await _add(session, make_tenant())
    owner = await _add(session, make_user(tenant=tenant, display_name="Owner"))
    viewer = await _add(session, make_user(tenant=tenant, display_name="Viewer"))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, owner, dataset)
    await _own(session, viewer, dataset, role=DatasetRole.VIEWER)

    assert await datasets_needing_new_owner(session, viewer) == []
    await delete_account(session, viewer)
    await session.commit()

    assert await session.get(User, viewer.id) is None
    assert await session.get(Dataset, dataset.id) is not None  # untouched


async def test_sole_owner_with_only_viewer_coworkers_blocks(session):
    # A dataset with other members but no other OWNER is still orphaned by the
    # deletion, so it must be resolved first (reassign a new owner or delete).
    tenant = await _add(session, make_tenant())
    owner = await _add(session, make_user(tenant=tenant))
    viewer = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, owner, dataset)
    await _own(session, viewer, dataset, role=DatasetRole.VIEWER)

    blocking = await datasets_needing_new_owner(session, owner)
    assert [d.id for d in blocking] == [dataset.id]


async def test_deletion_cascades_personal_records(session):
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    other = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, other, dataset)  # co-owner so nothing blocks
    await _own(session, user, dataset, role=DatasetRole.VIEWER)
    await _add(session, make_note(dataset=dataset, user=user))
    await _add(session, Comment(dataset_id=dataset.id, user_id=user.id, content="hi"))

    await delete_account(session, user)
    await session.commit()

    remaining_comments = (
        (await session.execute(select(Comment).where(Comment.user_id == user.id))).scalars().all()
    )
    assert remaining_comments == []
    assert await session.get(User, user.id) is None
    assert await session.get(Dataset, dataset.id) is not None  # shared dataset kept


async def test_sole_non_owner_member_blocks(session):
    # A dataset whose only member is the user -- even as a non-owner -- would be
    # left with no members, and no owner, so it must be resolved first. The
    # earlier check only looked at OWNER memberships and missed this.
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, user, dataset, role=DatasetRole.CURATOR)

    blocking = await datasets_needing_new_owner(session, user)
    assert [d.id for d in blocking] == [dataset.id]
    with pytest.raises(AccountDeletionBlockedError):
        await delete_account(session, user)
