"""Per-user dataset counts and last sign-in on the admin dashboard.

Both are reported per user, so the failure that matters is attributing a count
or a date to the wrong person. These tests assert the value attached to each
named user rather than a total, which a mis-joined query would still get right.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import User
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.routes.admin import _dataset_counts_by_user, record_login
from tests.factories import make_dataset, make_tenant, make_user


async def _account(session: AsyncSession, sub: str, email: str) -> User:
    """A tenant plus its user, as the hub creates them on first sign-in."""
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=email)
    session.add(user)
    await session.flush()
    return user


async def test_counts_are_attributed_to_the_owning_user(session: AsyncSession) -> None:
    alice = await _account(session, "sub-alice", "alice@example.org")
    bob = await _account(session, "sub-bob", "bob@example.org")
    alice_tenant = await _tenant_of(session, alice)
    for _ in range(3):
        session.add(make_dataset(tenant=alice_tenant))
    session.add(make_dataset(tenant=await _tenant_of(session, bob)))
    await session.commit()

    counts = await _dataset_counts_by_user(session)

    assert counts[alice.id] == 3
    assert counts[bob.id] == 1


async def _tenant_of(session: AsyncSession, user: User):
    from metaseed_hub.models import Tenant

    return await session.get(Tenant, user.tenant_id)


async def test_deleted_datasets_are_not_counted(session: AsyncSession) -> None:
    """A soft-deleted dataset is invisible to its owner, so counting it would
    overstate what the user has."""
    alice = await _account(session, "sub-alice-del", "alice@example.org")
    tenant = await _tenant_of(session, alice)
    kept = make_dataset(tenant=tenant)
    gone = make_dataset(tenant=tenant)
    gone.deleted_at = datetime.now(UTC)
    session.add_all([kept, gone])
    await session.commit()

    counts = await _dataset_counts_by_user(session)

    assert counts[alice.id] == 1


async def test_a_user_with_no_datasets_is_absent_rather_than_wrong(
    session: AsyncSession,
) -> None:
    """The template renders 0 for a missing key; the query must not invent a row
    with a count of 1 by joining the wrong way."""
    user = await _account(session, "sub-empty", "empty@example.org")
    await session.commit()

    counts = await _dataset_counts_by_user(session)

    assert counts.get(user.id, 0) == 0


async def test_record_login_stamps_the_signing_in_user(session: AsyncSession) -> None:
    alice = await _account(session, "sub-login-a", "alice@example.org")
    bob = await _account(session, "sub-login-b", "bob@example.org")
    await session.commit()
    assert alice.last_login_at is None

    before = datetime.now(UTC)
    await record_login(
        session, TokenUser(sub="sub-login-a", email="alice@example.org", name="A", roles=[])
    )

    await session.refresh(alice)
    await session.refresh(bob)
    assert alice.last_login_at is not None
    assert alice.last_login_at >= before - timedelta(seconds=1)
    assert bob.last_login_at is None, "only the signing-in user is stamped"


async def test_a_second_sign_in_moves_the_timestamp_forward(session: AsyncSession) -> None:
    user = await _account(session, "sub-twice", "twice@example.org")
    await session.commit()
    token = TokenUser(sub="sub-twice", email="twice@example.org", name="T", roles=[])

    await record_login(session, token)
    await session.refresh(user)
    first = user.last_login_at

    user.last_login_at = datetime.now(UTC) - timedelta(days=2)
    await session.commit()
    await record_login(session, token)
    await session.refresh(user)

    assert first is not None
    assert user.last_login_at is not None
    assert user.last_login_at > user.created_at - timedelta(days=1)
    assert (datetime.now(UTC) - user.last_login_at) < timedelta(minutes=1)


async def test_record_login_for_an_unknown_subject_is_a_no_op(session: AsyncSession) -> None:
    """The user row is created lazily on the first page, so the callback can run
    before it exists. That must not raise and abort the sign-in."""
    await _account(session, "sub-known", "known@example.org")
    await session.commit()

    await record_login(
        session, TokenUser(sub="nobody-here", email="x@example.org", name="X", roles=[])
    )

    users = (await session.execute(select(User))).scalars().all()
    assert [u.last_login_at for u in users] == [None]


async def test_a_database_failure_does_not_break_sign_in(session: AsyncSession) -> None:
    """Recording the login is bookkeeping; if it fails the user must still get in."""
    broken = Mock()
    broken.execute = Mock(side_effect=RuntimeError("database gone"))

    await record_login(broken, TokenUser(sub="s", email="e@example.org", name="N", roles=[]))
