"""The admin directory counts each user's published specifications.

Counted by author, not by account. The two differ: publishing a draft shared
from another account puts the specification in *that* account while
recording the publisher as its author, so counting by tenant credits the wrong
person — which is exactly the case that occurred in production.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecStatus
from metaseed_hub.ui.routes.admin import _spec_counts_by_user
from tests.factories import make_spec, make_tenant, make_user


async def _user(session: AsyncSession, *, slug: str, email: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=email)
    session.add(user)
    await session.commit()
    return tenant, user


async def test_a_users_published_specs_are_counted(session: AsyncSession) -> None:
    tenant, user = await _user(session, slug="cnt00001", email="a@example.org")
    session.add_all(
        [
            make_spec(tenant=tenant, created_by=user, name="One"),
            make_spec(tenant=tenant, created_by=user, name="Two"),
        ]
    )
    await session.commit()

    assert (await _spec_counts_by_user(session))[user.id] == 2


async def test_a_user_with_none_is_absent(session: AsyncSession) -> None:
    """Callers render 0 for a missing key rather than the query inventing rows."""
    _tenant, user = await _user(session, slug="cnt00002", email="b@example.org")

    assert user.id not in await _spec_counts_by_user(session)


async def test_the_author_is_credited_not_the_account(
    session: AsyncSession,
) -> None:
    """The production case: authored by one person, living in another's
    account. Counting by tenant would credit the wrong user."""
    owner_tenant, owner = await _user(session, slug="cnt00003", email="owner@example.org")
    _author_tenant, author = await _user(session, slug="cnt00004", email="author@example.org")
    session.add(make_spec(tenant=owner_tenant, created_by=author, name="Crossed"))
    await session.commit()

    counts = await _spec_counts_by_user(session)

    assert counts.get(author.id) == 1, "the person who wrote it"
    assert owner.id not in counts, "not the account it happens to sit in"


async def test_a_withdrawn_spec_is_not_counted(session: AsyncSession) -> None:
    """The column reports what is actually published."""
    tenant, user = await _user(session, slug="cnt00005", email="c@example.org")
    spec = make_spec(tenant=tenant, created_by=user, name="Gone")
    session.add(spec)
    await session.commit()
    spec.soft_delete()
    await session.commit()

    assert user.id not in await _spec_counts_by_user(session)


async def test_an_unpublished_spec_is_not_counted(session: AsyncSession) -> None:
    tenant, user = await _user(session, slug="cnt00006", email="d@example.org")
    session.add(make_spec(tenant=tenant, created_by=user, name="Draft", status=SpecStatus.DRAFT))
    await session.commit()

    assert user.id not in await _spec_counts_by_user(session)
