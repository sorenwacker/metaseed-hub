"""Saving a draft must not fail because another draft shares its spec's name.

A draft's row name is rewritten from its spec on every save. Two drafts whose
specs carry the same name therefore collided on
``uq_spec_drafts_tenant_user_name``, and the IntegrityError took down saving,
deleting a field, and importing alike -- the draft became unsavable and the work
in it unreachable. Seen in production: a user with two drafts of
``acdc_metadata_architecture`` could not save either.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.spec_builder.access import save_state_to_draft
from tests.factories import make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _two_drafts_sharing_a_spec_name(session: AsyncSession):
    """One user, two drafts whose specs are both named ``shared-spec``."""
    sub = f"sub-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(user)
    await session.flush()
    first = make_spec_draft(tenant=tenant, user=user, name="shared-spec")
    second = make_spec_draft(tenant=tenant, user=user, name="shared-spec-other")
    session.add_all([first, second])
    await session.commit()
    return user, first, second


def _state_named(name: str):
    """A builder state whose spec carries ``name``."""
    from metaseed.specs.schema import ProfileSpec

    from metaseed_hub.ui.spec_builder.state import SpecBuilderState

    state = SpecBuilderState()
    state.spec = ProfileSpec(
        version="1.0",
        name=name,
        display_name=name,
        description="d",
        ontology="T",
        root_entity="Sample",
        entities={},
    )
    return state


async def _name_of(session: AsyncSession, draft_id: str) -> str:
    result = await session.execute(select(SpecDraft.name).where(SpecDraft.id == draft_id))
    return result.scalar_one()


async def test_saving_onto_a_taken_name_still_saves(session: AsyncSession) -> None:
    user, first, second = await _two_drafts_sharing_a_spec_name(session)

    await save_state_to_draft(
        session, _state_named("shared-spec"), second, expected_revision=second.updated_at
    )

    saved = await _name_of(session, second.id)
    assert saved != "shared-spec", "the other draft holds that name"
    assert saved.startswith("shared-spec-"), saved
    assert await _name_of(session, first.id) == "shared-spec", "the other draft is untouched"


async def test_the_suffixed_name_is_stable_across_saves(session: AsyncSession) -> None:
    """A counter would walk -2, -3, -4 as the same draft was saved again."""
    _user, _first, second = await _two_drafts_sharing_a_spec_name(session)

    names = []
    for _ in range(3):
        await session.refresh(second)
        await save_state_to_draft(
            session, _state_named("shared-spec"), second, expected_revision=second.updated_at
        )
        names.append(await _name_of(session, second.id))

    assert len(set(names)) == 1, names


async def test_a_free_name_is_used_unchanged(session: AsyncSession) -> None:
    _user, _first, second = await _two_drafts_sharing_a_spec_name(session)

    await save_state_to_draft(
        session, _state_named("quite-free"), second, expected_revision=second.updated_at
    )

    assert await _name_of(session, second.id) == "quite-free"
