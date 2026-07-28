"""An author must be able to find a specification they published.

Reproduces a real incident. A draft shared from one workspace was published by
someone else; ``publish_draft`` writes ``Spec(tenant_id=draft.tenant_id)`` with
``created_by_id`` set to whoever published it, so the specification landed in
the workspace it was shared from. The Specs page filtered on the caller's own
workspace alone, so the author's work vanished on publish with no message, no
error, and nothing to find. It was recoverable only by reading the database.

The page must therefore show a specification to its author wherever it lives,
and name the workspace it lives in so the split is visible rather than inferred.
"""

from __future__ import annotations

from metaseed.specs.schema import ProfileSpec
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec, SpecStatus
from metaseed_hub.ui.spec_builder.access import workspace_owner
from tests.factories import make_spec, make_tenant, make_user


def _spec_data() -> dict:
    return {"spec": ProfileSpec(name="T", version="1.0").model_dump(mode="json")}


async def _listed_for(session: AsyncSession, user_id: str, tenant_id: str) -> list[Spec]:
    """Exactly what ``spec_builder_list`` selects."""
    result = await session.execute(
        select(Spec).where(
            or_(Spec.tenant_id == tenant_id, Spec.created_by_id == user_id),
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
        )
    )
    return list(result.scalars().all())


async def _two_workspaces(session: AsyncSession):
    """An owner and an author who is not the owner."""
    owner_tenant = make_tenant(slug="owner001")
    author_tenant = make_tenant(slug="author01")
    session.add_all([owner_tenant, author_tenant])
    await session.flush()
    owner = make_user(tenant=owner_tenant, email="karen@example.org")
    author = make_user(tenant=author_tenant, email="soren@example.org")
    session.add_all([owner, author])
    await session.commit()
    return owner_tenant, owner, author_tenant, author


async def test_the_author_finds_a_spec_published_into_another_workspace(
    session: AsyncSession,
) -> None:
    """The incident: published, then gone from the author's page entirely."""
    owner_tenant, _owner, author_tenant, author = await _two_workspaces(session)
    # As publish_draft writes it: the shared draft's workspace, the author's id.
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    await session.commit()

    listed = await _listed_for(session, author.id, author_tenant.id)

    assert [s.name for s in listed] == ["acdc"], "the author must find their own work"


async def test_the_owner_still_sees_it(session: AsyncSession) -> None:
    """The workspace it lives in keeps showing it — this is not a move."""
    owner_tenant, owner, _author_tenant, author = await _two_workspaces(session)
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    await session.commit()

    listed = await _listed_for(session, owner.id, owner_tenant.id)

    assert [s.name for s in listed] == ["acdc"]


async def test_an_unrelated_person_sees_neither(session: AsyncSession) -> None:
    """Authorship widens the query, so it must not widen it to everyone."""
    owner_tenant, _owner, _author_tenant, author = await _two_workspaces(session)
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    stranger_tenant = make_tenant(slug="stranger")
    session.add(stranger_tenant)
    await session.flush()
    stranger = make_user(tenant=stranger_tenant, email="nobody@example.org")
    session.add(stranger)
    await session.commit()

    listed = await _listed_for(session, stranger.id, stranger_tenant.id)

    assert listed == []


async def test_the_workspace_owner_is_identifiable(session: AsyncSession) -> None:
    """So the page can say whose workspace a spec is in, and who to contact."""
    owner_tenant, owner, _author_tenant, _author = await _two_workspaces(session)

    found = await workspace_owner(session, owner_tenant.id)

    assert found is not None
    assert found.id == owner.id
    assert found.email == "karen@example.org"


async def test_a_removed_spec_stays_hidden_from_its_author(
    session: AsyncSession,
) -> None:
    """Widening by authorship must not resurrect withdrawn or admin-removed
    specifications."""
    owner_tenant, _owner, author_tenant, author = await _two_workspaces(session)
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    await session.commit()
    spec.soft_delete()
    await session.commit()

    assert await _listed_for(session, author.id, author_tenant.id) == []
