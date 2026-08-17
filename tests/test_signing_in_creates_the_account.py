"""Signing in creates the account, rather than some later page doing it.

Accounts were created lazily, on the first page that happened to call
`ensure_tenant_and_user`. The page a new user lands on is `/hub/home` — chosen
precisely *because* they have no account yet — and it renders a static guide
without provisioning anything.

So a colleague could sign in, read the guide, and still not exist: sharing
resolves people by the address on their account, so nobody could share
anything with them. They looked signed in and were unreachable, and the only
cure was to wander onto a different page.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Tenant, User
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.routes.auth import _after_sign_in

pytestmark = pytest.mark.asyncio


def _newcomer() -> TokenUser:
    return TokenUser(
        sub="newcomer-kc",
        email="Newcomer@Example.org",
        name="A Newcomer",
        roles=[],
    )


async def test_a_first_sign_in_creates_the_account(session: AsyncSession) -> None:
    user = _newcomer()

    await _after_sign_in(session, user)

    found = (
        await session.execute(select(User).where(User.keycloak_id == "newcomer-kc"))
    ).scalar_one_or_none()
    assert found is not None
    assert found.email == "newcomer@example.org", "the address is stored normalized"


async def test_a_first_sign_in_creates_their_tenant(session: AsyncSession) -> None:
    await _after_sign_in(session, _newcomer())

    slug = tenant_slug_for("newcomer-kc")
    found = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    assert found is not None


async def test_a_newcomer_lands_on_the_guide(session: AsyncSession) -> None:
    """Provisioning must not change where a user with no work is sent."""
    landing = await _after_sign_in(session, _newcomer())

    assert landing == "/hub/home"


async def test_signing_in_again_does_not_duplicate_the_account(session: AsyncSession) -> None:
    user = _newcomer()
    await _after_sign_in(session, user)
    await _after_sign_in(session, user)

    found = (await session.execute(select(User).where(User.keycloak_id == "newcomer-kc"))).scalars()
    assert len(list(found)) == 1
