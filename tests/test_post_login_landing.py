"""Where a user lands after signing in.

The callback sent everyone to the dataset list, so a first-time user with
nothing met a bare "No datasets yet" screen — while the Home guide that explains
what to do was reachable only from the logo, which a newcomer would not know to
click. The person who most needs the guide never saw it.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.routes.auth import _post_login_landing
from tests.factories import make_dataset, make_spec, make_tenant, make_user


def _token_user(sub: str):
    """The shape _post_login_landing reads: a subject with .sub."""
    return SimpleNamespace(sub=sub, email="u@example.org", name="U", roles=[])


async def _tenant_for(session: AsyncSession, sub: str):
    """A tenant whose slug matches what the callback derives from the subject."""
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email="u@example.org")
    session.add(user)
    await session.commit()
    return tenant, user


async def test_a_brand_new_user_lands_on_the_guide(session: AsyncSession) -> None:
    """No account yet — definitely nothing to show."""
    where = await _post_login_landing(session, _token_user("new-subject-1"))

    assert where == "/hub/home"


async def test_a_user_with_no_content_lands_on_the_guide(session: AsyncSession) -> None:
    """Has a account from a prior visit but has created nothing."""
    await _tenant_for(session, "empty-subject-1")

    where = await _post_login_landing(session, _token_user("empty-subject-1"))

    assert where == "/hub/home"


async def test_a_user_with_a_dataset_lands_on_their_work(session: AsyncSession) -> None:
    tenant, _user = await _tenant_for(session, "has-data-1")
    session.add(make_dataset(tenant=tenant, name="mine"))
    await session.commit()

    where = await _post_login_landing(session, _token_user("has-data-1"))

    assert where == "/hub/"


async def test_a_user_with_a_spec_lands_on_their_work(session: AsyncSession) -> None:
    tenant, user = await _tenant_for(session, "has-spec-1")
    session.add(make_spec(tenant=tenant, created_by=user, name="MySpec"))
    await session.commit()

    where = await _post_login_landing(session, _token_user("has-spec-1"))

    assert where == "/hub/"


async def test_a_deleted_dataset_does_not_count_as_content(
    session: AsyncSession,
) -> None:
    """A user who created then removed everything should be onboarded again,
    not stranded on an empty list."""
    tenant, _user = await _tenant_for(session, "deleted-1")
    dataset = make_dataset(tenant=tenant, name="gone")
    session.add(dataset)
    await session.commit()
    dataset.soft_delete()
    await session.commit()

    where = await _post_login_landing(session, _token_user("deleted-1"))

    assert where == "/hub/home"
