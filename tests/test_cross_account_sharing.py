"""Sharing resolves an invitee who lives in another tenant.

Every account gets its own tenant (``tenant_slug_for`` hashes the OIDC subject),
so an invitee is *always* outside the sharer's tenant. A tenant-scoped invitee
lookup therefore matched nobody and reported "User not found. They must log in
first before you can share." at a person who was already signed in.

Emails are stored lowercased and matched without regard to capitalisation, so
the address a sharer types resolves whatever casing the identity provider sent.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import (
    Tenant,
    User,
)
from metaseed_hub.ui.dependencies import (
    DuplicateAccountEmailError,
    ensure_tenant_and_user,
    tenant_slug_for,
)
from metaseed_hub.ui.helpers import get_or_create_csrf_token
from tests.factories import make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))


async def _own_tenant_user(
    session: AsyncSession, *, email: str | None = None
) -> tuple[Tenant, User, str]:
    """An account in its own tenant, keyed by subject like real provisioning."""
    sub = f"sub-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=email)
    session.add(user)
    await session.flush()
    return tenant, user, sub


# The sharing behaviours that used to be asserted here — sharing across
# accounts, case-insensitive addresses, an address nobody has signed in with —
# now live in tests/test_sharing.py, which runs each of them against datasets,
# drafts and published specifications rather than one kind at a time.


async def test_provisioning_stores_the_email_lowercased(session: AsyncSession) -> None:
    """Lowercase storage is what keeps one address to one row under the constraint."""
    sub = f"sub-{uuid4().hex[:8]}"
    token = TokenUser(sub=sub, email="Mixed.Case@Example.ORG", name="M", roles=[])

    _, db_user = await ensure_tenant_and_user(session, token)

    assert db_user.email == "mixed.case@example.org"


async def test_a_new_subject_reusing_a_known_email_is_refused_not_a_500(
    session: AsyncSession,
) -> None:
    """One address to one account means a second account cannot claim it.

    An identity provider that reissues subjects (a rebuilt realm) sends a known
    address under a new subject. That must be a stated refusal an admin can act
    on, not an IntegrityError surfacing as a 500 on every authenticated page,
    and never a silent rebind of the existing account to the new subject.
    """
    _, existing, _ = await _own_tenant_user(session, email="returning@example.org")
    await session.commit()
    new_subject = TokenUser(
        sub=f"sub-{uuid4().hex[:8]}", email="Returning@example.org", name="R", roles=[]
    )

    with pytest.raises(DuplicateAccountEmailError):
        await ensure_tenant_and_user(session, new_subject)

    await session.rollback()
    kept = (
        await session.execute(select(User).where(User.email == "returning@example.org"))
    ).scalar_one()
    assert kept.keycloak_id == existing.keycloak_id


async def test_one_account_exists_per_email_address(session: AsyncSession) -> None:
    """Global uniqueness is what makes an unscoped lookup unambiguous."""
    tenant_a = make_tenant(slug="uniq-a")
    tenant_b = make_tenant(slug="uniq-b")
    session.add_all([tenant_a, tenant_b])
    await session.flush()
    session.add(make_user(tenant=tenant_a, email="shared@example.org"))
    await session.flush()
    session.add(make_user(tenant=tenant_b, email="shared@example.org"))

    with pytest.raises(IntegrityError):
        await session.flush()
