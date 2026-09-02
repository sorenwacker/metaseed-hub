"""Withdrawing a published spec back to a private draft.

Publishing is one-way: it replaces an editable draft with an immutable published
specification and deletes the draft. A user published something by mistake and
there was no way back short of hand-editing the database.

The assertions here are about *whether the spec is still listed and offered*
afterwards, not merely that a flag was set: a withdrawal that leaves the spec in
the tenant listing has not withdrawn it.
"""

from __future__ import annotations

import pytest
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec, SpecDraft, SpecStatus
from metaseed_hub.ui.spec_builder.access import unpublish_spec
from tests.factories import make_spec, make_spec_draft, make_tenant, make_user


def _spec_data(name: str = "Secret", version: str = "1.0") -> dict:
    """A minimal serialized SpecBuilderState, as publish_draft writes it."""
    return {"spec": ProfileSpec(name=name, version=version).model_dump(mode="json")}


async def _published(session: AsyncSession, *, slug: str = "acme1234"):
    """A tenant with a published spec, plus a second user as a non-owner."""
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    # Keyed by slug: an address identifies one account hub-wide, so two calls
    # here must not hand the same address to two people.
    author = make_user(tenant=tenant, email=f"author-{slug}@example.org")
    colleague = make_user(tenant=tenant, email=f"colleague-{slug}@example.org")
    session.add_all([author, colleague])
    await session.flush()
    spec = make_spec(tenant=tenant, created_by=author, spec_data=_spec_data())
    session.add(spec)
    await session.commit()
    return tenant, author, colleague, spec


async def _visible_to_tenant(session: AsyncSession, tenant_id: str) -> list[Spec]:
    """Published specs as the Specs page and the new-dataset form list them."""
    result = await session.execute(
        select(Spec).where(
            Spec.tenant_id == tenant_id,
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
        )
    )
    return list(result.scalars().all())


async def test_a_withdrawn_spec_leaves_the_tenant_listing(session: AsyncSession) -> None:
    """The whole point: it must stop being listed and offered."""
    tenant, author, _colleague, spec = await _published(session)
    assert await _visible_to_tenant(session, tenant.id) != [], "precondition"

    await unpublish_spec(session, spec, author.id)

    assert await _visible_to_tenant(session, tenant.id) == []


async def test_the_specification_comes_back_as_a_private_draft(
    session: AsyncSession,
) -> None:
    """Withdrawing must not cost the author their work."""
    _tenant, author, _colleague, spec = await _published(session)

    draft = await unpublish_spec(session, spec, author.id)

    assert draft.user_id == author.id, "the draft belongs to whoever withdrew it"
    assert draft.name == spec.name
    stored = await session.get(SpecDraft, draft.id)
    assert stored is not None
    assert stored.spec_data["spec"]["name"] == "Secret", "the specification survived"


async def test_the_withdrawn_spec_is_kept_on_record(session: AsyncSession) -> None:
    """Soft-deleted, not erased, so an admin can account for what existed."""
    _tenant, author, _colleague, spec = await _published(session)

    await unpublish_spec(session, spec, author.id)

    still_there = await session.get(Spec, spec.id)
    assert still_there is not None
    assert still_there.deleted_at is not None


async def test_the_same_specification_can_be_published_again(
    session: AsyncSession,
) -> None:
    """A withdrawn spec must stop reserving its (tenant, name, version) slot.

    ``uq_specs_tenant_name_version`` was a plain unique constraint, so the
    soft-deleted row still occupied the name and republishing an unchanged
    specification failed with an IntegrityError on a row nobody could see.
    """
    tenant, author, _colleague, spec = await _published(session)
    await unpublish_spec(session, spec, author.id)

    republished = make_spec(
        tenant=tenant,
        created_by=author,
        name=spec.name,
        version=spec.version,
        spec_data=_spec_data(),
    )
    session.add(republished)
    await session.commit()

    assert len(await _visible_to_tenant(session, tenant.id)) == 1


async def test_a_spec_with_no_specification_is_not_withdrawn(
    session: AsyncSession,
) -> None:
    """Failing closed: withdrawing must not destroy what it cannot hand back."""
    tenant = make_tenant(slug="empty123")
    session.add(tenant)
    await session.flush()
    author = make_user(tenant=tenant)
    session.add(author)
    await session.flush()
    spec = make_spec(tenant=tenant, created_by=author, spec_data={})
    session.add(spec)
    await session.commit()

    with pytest.raises(ValueError, match="no specification"):
        await unpublish_spec(session, spec, author.id)

    assert len(await _visible_to_tenant(session, tenant.id)) == 1
    assert (await session.get(Spec, spec.id)).deleted_at is None


async def test_withdrawal_does_not_touch_another_tenant(session: AsyncSession) -> None:
    """Scoping: one tenant's withdrawal must not disturb another's specs."""
    tenant_a, author_a, _c, spec_a = await _published(session, slug="aaaa1111")
    tenant_b, _author_b, _c2, _spec_b = await _published(session, slug="bbbb2222")

    await unpublish_spec(session, spec_a, author_a.id)

    assert await _visible_to_tenant(session, tenant_a.id) == []
    assert len(await _visible_to_tenant(session, tenant_b.id)) == 1


async def test_a_plain_colleague_may_not_withdraw_someone_elses_spec(
    session: AsyncSession,
) -> None:
    """The route gates on ``can_edit_spec``. Tenant membership alone must not be
    enough, or any colleague could retract a release they did not make."""
    from metaseed_hub.ui.spec_builder.access import can_edit_spec

    _tenant, author, colleague, spec = await _published(session, slug="gate1234")

    assert await can_edit_spec(session, author.id, spec.id) is True
    assert await can_edit_spec(session, colleague.id, spec.id) is False


async def test_withdrawing_over_a_draft_of_the_same_name_does_not_collide(
    session: AsyncSession,
) -> None:
    """Draft names are unique per (tenant, user). Unpublishing a spec whose name
    matches a draft the author already holds hit the unique index; the withdrawn
    spec now comes back under a collision-avoiding name."""
    tenant, author, _colleague, spec = await _published(session)
    # A draft the author already holds under the spec's own name.
    session.add(make_spec_draft(tenant=tenant, user=author, name=spec.name, version="9.9"))
    await session.commit()
    clashing = spec.name

    draft = await unpublish_spec(session, spec, author.id)

    assert draft.name != clashing, "the name was deduplicated"
    assert draft.spec_data["spec"]["name"] == "Secret", "the specification is unchanged"
    drafts = (
        (await session.execute(select(SpecDraft).where(SpecDraft.user_id == author.id)))
        .scalars()
        .all()
    )
    assert len(drafts) == 2, "both drafts coexist"
    assert clashing in {d.name for d in drafts}, "the original draft keeps its name"
