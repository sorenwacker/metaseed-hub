"""A first sign-in is a sign-in, and the admin page should say so.

`record_login` stamps `last_login_at`, and it ran before the account existed —
so for a brand-new user it found no row and did nothing. Their very first
sign-in went unrecorded, and the admin directory showed "Never" for someone who
had just arrived, which reads as a stale or broken account rather than a new
one.

Provisioning now happens first, so the stamp lands on the row it belongs to.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import User
from metaseed_hub.ui.routes.auth import _after_sign_in

pytestmark = pytest.mark.asyncio


async def test_the_very_first_sign_in_is_stamped(session: AsyncSession) -> None:
    user = TokenUser(sub="firsttimer-kc", email="first@example.org", name="First", roles=[])

    await _after_sign_in(session, user)

    found = (
        await session.execute(select(User).where(User.keycloak_id == "firsttimer-kc"))
    ).scalar_one()
    assert found.last_login_at is not None, "a new account shows 'Never' until it signs in twice"
