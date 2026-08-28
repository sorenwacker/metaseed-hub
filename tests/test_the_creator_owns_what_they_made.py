"""Whoever created a draft or a spec owns it, membership row or not.

Sharing gates every owner-only control on `role_of`, which reads the
membership table alone. Nothing writes a membership row when a spec draft is
created — `create_draft` sets `user_id` and stops — so the creator had no role
at all. The members panel renders its add-member form under
`{% if viewer_is_owner %}`, which meant the person who made a draft could not
share it, and the browser test for exactly that has failed since the
owner-gating arrived.

Ownership is one rule for everything, in `role_of`: an explicit membership,
else the creator where the model records one, else the person whose account
the item lives in.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft, SpecMember
from metaseed_hub.sharing import Role, resource_for, role_of
from tests.factories import make_dataset, make_spec, make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _tenant_and_user(session: AsyncSession, slug: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=f"{slug}-kc")
    session.add(user)
    await session.flush()
    return tenant, user


async def test_the_creator_of_a_draft_owns_it(session: AsyncSession) -> None:
    tenant, user = await _tenant_and_user(session, "draftowner")
    draft = SpecDraft(
        tenant_id=tenant.id,
        user_id=user.id,
        name="Fieldbook",
        version="1.0",
        spec_data={},
    )
    session.add(draft)
    await session.commit()

    assert await role_of(session, resource_for("draft"), draft.id, user.id) is Role.OWNER


async def test_someone_else_does_not_own_that_draft(session: AsyncSession) -> None:
    """The creator column grants ownership to exactly one person."""
    tenant, user = await _tenant_and_user(session, "draftowner2")
    _other_tenant, stranger = await _tenant_and_user(session, "draftstranger")
    draft = SpecDraft(
        tenant_id=tenant.id,
        user_id=user.id,
        name="Fieldbook",
        version="1.0",
        spec_data={},
    )
    session.add(draft)
    await session.commit()

    assert await role_of(session, resource_for("draft"), draft.id, stranger.id) is None


async def test_the_creator_of_a_spec_owns_it(session: AsyncSession) -> None:
    tenant, user = await _tenant_and_user(session, "specowner")
    spec = make_spec(tenant=tenant, created_by=user)
    session.add(spec)
    await session.commit()

    assert await role_of(session, resource_for("spec"), spec.id, user.id) is Role.OWNER


async def test_the_accounts_user_owns_a_dataset_that_lives_there(session: AsyncSession) -> None:
    """Datasets record no creator; the account they live in is theirs."""
    tenant, user = await _tenant_and_user(session, "dsowner")
    _other_tenant, stranger = await _tenant_and_user(session, "dsstranger")
    dataset = make_dataset(tenant=tenant)
    session.add(dataset)
    await session.commit()
    assert await role_of(session, resource_for("dataset"), dataset.id, user.id) is Role.OWNER
    assert await role_of(session, resource_for("dataset"), dataset.id, stranger.id) is None


async def test_an_explicit_membership_still_wins_for_a_spec(session: AsyncSession) -> None:
    """A creator demoted by an explicit row keeps the role that row states."""
    tenant, user = await _tenant_and_user(session, "specviewer")
    _other, colleague = await _tenant_and_user(session, "speccolleague")
    spec = make_spec(tenant=tenant, created_by=user)
    session.add(spec)
    await session.flush()
    session.add(SpecMember(spec_id=spec.id, user_id=colleague.id, role=Role.VIEWER))
    await session.commit()

    assert await role_of(session, resource_for("spec"), spec.id, colleague.id) is Role.VIEWER
