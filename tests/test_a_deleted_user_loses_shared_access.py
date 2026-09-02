"""A soft-deleted user is nonexistent to every authorization path.

`tokens.py` states the invariant: "every other lookup in the hub treats such a
user as nonexistent, and authentication must not be the one credential path
that outlives the account". The MCP layer enforces it. The three access-ladder
helpers did not — they resolved the caller by `keycloak_id` alone.

Admin soft-deletion sets `deleted_at` but deliberately leaves `DatasetMember`
rows in place, so a soft-deleted user holding a still-valid OIDC cookie kept
membership-based access to shared datasets through every UI, API, and
websocket route that authorizes through this module. Deleting the account did
not end the session's reach into other people's data.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.access import (
    get_dataset_for_editor,
    get_dataset_for_user,
    tenant_slug_for,
    verify_tenant_access,
)
from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, DatasetMember, Role, User
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _shared_dataset_with_a_member(
    session: AsyncSession,
) -> tuple[Dataset, User, TokenUser]:
    """An owner's dataset shared with a second user as EDITOR."""
    owner_sub = f"owner-{uuid4().hex[:8]}"
    member_sub = f"member-{uuid4().hex[:8]}"

    owner_tenant = make_tenant(slug=tenant_slug_for(owner_sub))
    member_tenant = make_tenant(slug=tenant_slug_for(member_sub))
    session.add_all([owner_tenant, member_tenant])
    await session.flush()

    owner = make_user(tenant=owner_tenant, keycloak_id=owner_sub)
    member = make_user(tenant=member_tenant, keycloak_id=member_sub)
    session.add_all([owner, member])
    await session.flush()

    dataset = make_dataset(tenant=owner_tenant)
    session.add(dataset)
    await session.flush()
    session.add(DatasetMember(dataset_id=dataset.id, user_id=member.id, role=Role.EDITOR))
    await session.commit()

    return dataset, member, TokenUser(sub=member_sub, email=member.email, name="M", roles=[])


async def test_a_soft_deleted_member_cannot_read_the_shared_dataset(
    session: AsyncSession,
) -> None:
    dataset, member, token = await _shared_dataset_with_a_member(session)
    member.soft_delete()
    await session.commit()

    with pytest.raises(HTTPException) as denied:
        await get_dataset_for_user(dataset.id, session, token)

    assert denied.value.status_code in (403, 404)


async def test_a_soft_deleted_member_cannot_edit_the_shared_dataset(
    session: AsyncSession,
) -> None:
    dataset, member, token = await _shared_dataset_with_a_member(session)
    member.soft_delete()
    await session.commit()

    with pytest.raises(HTTPException) as denied:
        await get_dataset_for_editor(dataset.id, session, token)

    assert denied.value.status_code in (403, 404)


async def test_a_live_member_still_has_access(session: AsyncSession) -> None:
    """The guard must not lock out the members it does not apply to."""
    dataset, _member, token = await _shared_dataset_with_a_member(session)

    assert (await get_dataset_for_user(dataset.id, session, token)).id == dataset.id
    assert (await get_dataset_for_editor(dataset.id, session, token)).id == dataset.id


async def test_a_soft_deleted_user_cannot_pass_the_tenant_gate(session: AsyncSession) -> None:
    """verify_tenant_access resolved the tenant from the token's subject hash
    alone and never looked at the User row, so a deleted user with a live OIDC
    token still passed it — the one ladder helper left outside the fix this
    module documents."""
    sub = f"gone-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(user)
    await session.commit()
    token = TokenUser(sub=sub, email=user.email, name="G", roles=[])

    assert (await verify_tenant_access(tenant.id, session, token)).id == tenant.id

    user.soft_delete()
    await session.commit()
    with pytest.raises(HTTPException) as denied:
        await verify_tenant_access(tenant.id, session, token)
    assert denied.value.status_code == 403
