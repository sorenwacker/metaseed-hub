"""An author must be able to find a specification they published.

Reproduces a real incident. A draft shared from one account was published by
someone else; ``publish_draft`` writes ``Spec(tenant_id=draft.tenant_id)`` with
``created_by_id`` set to whoever published it, so the specification landed in
the account it was shared from. The Specs page filtered on the caller's own
account alone, so the author's work vanished on publish with no message, no
error, and nothing to find. It was recoverable only by reading the database.

The page must therefore show a specification to its author wherever it lives,
and name the account it lives in so the split is visible rather than inferred.
"""

from __future__ import annotations

from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec, SpecStatus
from metaseed_hub.sharing import account_owner
from tests.factories import make_spec, make_tenant, make_user


def _spec_data() -> dict:
    return {"spec": ProfileSpec(name="T", version="1.0").model_dump(mode="json")}


async def _listed_for(session: AsyncSession, user_id: str, tenant_id: str) -> list[Spec]:
    """Exactly what ``spec_builder_list`` selects: every published spec.

    The arguments are kept so each test still states whose page is rendered,
    even though the query no longer narrows by them - publishing shares a
    specification with everyone on the platform.
    """
    result = await session.execute(
        select(Spec).where(
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
        )
    )
    return list(result.scalars().all())


async def _two_accounts(session: AsyncSession):
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


async def test_the_author_finds_a_spec_published_into_another_account(
    session: AsyncSession,
) -> None:
    """The incident: published, then gone from the author's page entirely."""
    owner_tenant, _owner, author_tenant, author = await _two_accounts(session)
    # As publish_draft writes it: the shared draft's account, the author's id.
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    await session.commit()

    listed = await _listed_for(session, author.id, author_tenant.id)

    assert [s.name for s in listed] == ["acdc"], "the author must find their own work"


async def test_the_owner_still_sees_it(session: AsyncSession) -> None:
    """The account it lives in keeps showing it — this is not a move."""
    owner_tenant, owner, _author_tenant, author = await _two_accounts(session)
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    await session.commit()

    listed = await _listed_for(session, owner.id, owner_tenant.id)

    assert [s.name for s in listed] == ["acdc"]


async def test_everyone_sees_a_published_spec(session: AsyncSession) -> None:
    """Publishing shares a specification with the whole platform, so someone
    unconnected to it sees it too. This assertion was previously the opposite:
    publishing used to be observable only to its author, which was never what
    it was meant to do."""
    owner_tenant, _owner, _author_tenant, author = await _two_accounts(session)
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    stranger_tenant = make_tenant(slug="stranger")
    session.add(stranger_tenant)
    await session.flush()
    stranger = make_user(tenant=stranger_tenant, email="nobody@example.org")
    session.add(stranger)
    await session.commit()

    listed = await _listed_for(session, stranger.id, stranger_tenant.id)

    assert [s.name for s in listed] == ["acdc"]


async def test_the_account_owner_is_identifiable(session: AsyncSession) -> None:
    """So the page can say whose account a spec is in, and who to contact."""
    owner_tenant, owner, _author_tenant, _author = await _two_accounts(session)

    found = await account_owner(session, owner_tenant.id)

    assert found is not None
    assert found.id == owner.id
    assert found.email == "karen@example.org"


async def test_a_removed_spec_stays_hidden_from_its_author(
    session: AsyncSession,
) -> None:
    """Widening by authorship must not resurrect withdrawn or admin-removed
    specifications."""
    owner_tenant, _owner, author_tenant, author = await _two_accounts(session)
    spec = make_spec(tenant=owner_tenant, created_by=author, name="acdc", spec_data=_spec_data())
    session.add(spec)
    await session.commit()
    spec.soft_delete()
    await session.commit()

    assert await _listed_for(session, author.id, author_tenant.id) == []
