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
    Role,
    Spec,
    SpecMember,
    User,
)
from metaseed_hub.repositories.account import (
    AccountDeletionBlockedError,
    datasets_needing_new_owner,
    delete_account,
    specs_needing_new_owner,
)
from tests.factories import make_dataset, make_spec, make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _add(session, obj):
    """Persist one object and flush so its generated id is available."""
    session.add(obj)
    await session.flush()
    return obj


async def _own(session, user, dataset, role=Role.OWNER):
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
    assert members[0].role == Role.OWNER


async def test_viewer_membership_does_not_block(session):
    tenant = await _add(session, make_tenant())
    owner = await _add(session, make_user(tenant=tenant, display_name="Owner"))
    viewer = await _add(session, make_user(tenant=tenant, display_name="Viewer"))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, owner, dataset)
    await _own(session, viewer, dataset, role=Role.VIEWER)

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
    await _own(session, viewer, dataset, role=Role.VIEWER)

    blocking = await datasets_needing_new_owner(session, owner)
    assert [d.id for d in blocking] == [dataset.id]


async def test_deletion_cascades_personal_records(session):
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    other = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, other, dataset)  # co-owner so nothing blocks
    await _own(session, user, dataset, role=Role.VIEWER)
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
    await _own(session, user, dataset, role=Role.EDITOR)

    blocking = await datasets_needing_new_owner(session, user)
    assert [d.id for d in blocking] == [dataset.id]
    with pytest.raises(AccountDeletionBlockedError):
        await delete_account(session, user)


async def _own_spec(session, user, spec, role=Role.OWNER):
    session.add(SpecMember(spec_id=spec.id, user_id=user.id, role=role))
    await session.flush()


async def test_sole_owned_spec_blocks_deletion(session):
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    spec = await _add(session, make_spec(tenant=tenant, created_by=user))
    await _own_spec(session, user, spec)

    blocking = await specs_needing_new_owner(session, user)
    assert [s.id for s in blocking] == [spec.id]
    with pytest.raises(AccountDeletionBlockedError) as exc:
        await delete_account(session, user)
    assert [s.id for s in exc.value.specs] == [spec.id]


async def test_co_owned_spec_allows_deletion_and_survives(session):
    tenant = await _add(session, make_tenant())
    leaving = await _add(session, make_user(tenant=tenant, display_name="Leaving"))
    staying = await _add(session, make_user(tenant=tenant, display_name="Staying"))
    spec = await _add(session, make_spec(tenant=tenant, created_by=leaving))
    await _own_spec(session, leaving, spec)
    await _own_spec(session, staying, spec)

    assert await specs_needing_new_owner(session, leaving) == []
    await delete_account(session, leaving)
    await session.commit()

    assert await session.get(User, leaving.id) is None
    assert await session.get(Spec, spec.id) is not None  # survives under co-owner


@pytest.mark.asyncio
async def test_a_dataset_the_user_already_deleted_does_not_block(session):
    """Every list view filters soft-deleted datasets out, so a blocker among
    them is one the user is told to reassign but cannot see — deleting your
    own solely-owned dataset and then your account was impossible."""
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, user, dataset)
    dataset.soft_delete()
    await session.commit()

    assert await datasets_needing_new_owner(session, user) == []
    await delete_account(session, user)
    await session.commit()


@pytest.mark.asyncio
async def test_a_spec_the_user_withdrew_does_not_block(session):
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    spec = await _add(session, make_spec(tenant=tenant, created_by=user))
    session.add(SpecMember(spec_id=spec.id, user_id=user.id, role=Role.OWNER))
    spec.soft_delete()
    await session.commit()

    assert await specs_needing_new_owner(session, user) == []


@pytest.mark.asyncio
async def test_erasure_takes_the_tenant_and_seek_connection_with_it(session):
    """Hub tenants are per user and named after them, and the SeekConnection
    holds their encrypted SEEK API key — both are personal data the module
    docstring claimed the cascade removed. It did not."""
    from metaseed_hub.models import SeekConnection, Tenant

    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    session.add(
        SeekConnection(
            tenant_id=tenant.id,
            url="https://seek.example",
            api_key_encrypted="sealed",
        )
    )
    await session.commit()
    tenant_id = tenant.id

    await delete_account(session, user)
    await session.commit()

    assert await session.get(Tenant, tenant_id) is None
    remaining = (
        (await session.execute(select(SeekConnection).where(SeekConnection.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    assert remaining == []


@pytest.mark.asyncio
async def test_erasure_removes_the_users_own_deleted_datasets(session):
    """A soft-deleted, solely-owned dataset is invisible and unowned — and it
    survived erasure with its full JSONB intact. Nothing in the codebase ever
    hard-deleted a Dataset, so 'right to erasure' left the data behind."""
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, user, dataset)
    dataset.soft_delete()
    await session.commit()
    dataset_id = dataset.id

    await delete_account(session, user)
    await session.commit()

    assert await session.get(Dataset, dataset_id) is None


@pytest.mark.asyncio
async def test_erasure_removes_the_users_own_withdrawn_specs(session):
    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    spec = await _add(session, make_spec(tenant=tenant, created_by=user))
    session.add(SpecMember(spec_id=spec.id, user_id=user.id, role=Role.OWNER))
    spec.soft_delete()
    await session.commit()
    spec_id = spec.id

    await delete_account(session, user)
    await session.commit()

    assert await session.get(Spec, spec_id) is None


@pytest.mark.asyncio
async def test_erasure_keeps_a_deleted_dataset_someone_else_also_owns(session):
    """Erasing the person must not destroy work that outlives them, and a
    co-owner can still restore a soft-deleted dataset."""
    tenant = await _add(session, make_tenant())
    other_tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    colleague = await _add(session, make_user(tenant=other_tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, user, dataset)
    await _own(session, colleague, dataset)
    dataset.soft_delete()
    await session.commit()
    dataset_id = dataset.id

    await delete_account(session, user)
    await session.commit()

    assert await session.get(Dataset, dataset_id) is not None


@pytest.mark.asyncio
async def test_a_tenant_holding_only_tombstones_is_erased_not_scrubbed(session):
    """The tenant survives to protect co-owned work. Rows nobody can see are
    not that, yet the retention probes counted them."""
    from metaseed_hub.models import Tenant

    tenant = await _add(session, make_tenant())
    user = await _add(session, make_user(tenant=tenant))
    dataset = await _add(session, make_dataset(tenant=tenant))
    await _own(session, user, dataset)
    dataset.soft_delete()
    await session.commit()
    tenant_id = tenant.id

    await delete_account(session, user)
    await session.commit()

    assert await session.get(Tenant, tenant_id) is None
