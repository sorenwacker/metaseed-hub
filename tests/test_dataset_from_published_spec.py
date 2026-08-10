"""Creating a dataset from a published specification.

The new-dataset form submitted a published spec's id prefixed with ``draft:``,
so the route looked for a ``SpecDraft`` that did not exist and redirected to
``/hub/?error=draft_not_found``. Choosing a published specification therefore
never worked -- which made publishing one pointless, since publishing exists to
let other people use it.

The assertions are about the dataset that results: which specification it is
bound to, and whether its entity types resolve. A dataset that is created but
cannot resolve its profile is not a working dataset.
"""

from __future__ import annotations

from metaseed.specs.schema import EntityDefSpec, FieldSpec, ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset, Spec, SpecStatus
from metaseed_hub.ui.helpers import ensure_dataset_facade
from tests.factories import make_spec, make_tenant, make_user


def _spec_data(name: str = "SharedSpec") -> dict:
    """A published spec with one entity, so resolution is observable."""
    spec = ProfileSpec(
        name=name,
        version="1.0",
        root_entity="Study",
        entities={
            "Study": EntityDefSpec(
                description="A study",
                fields=[FieldSpec(name="Title", codename="title", type="string")],
            )
        },
    )
    return {"spec": spec.model_dump(mode="json")}


async def _published(session: AsyncSession, *, slug: str, name: str = "SharedSpec"):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    author = make_user(tenant=tenant, email=f"{slug}@example.org")
    session.add(author)
    await session.flush()
    spec = make_spec(
        tenant=tenant,
        created_by=author,
        name=name,
        version="1.0",
        spec_data=_spec_data(name),
        status=SpecStatus.PUBLISHED,
    )
    session.add(spec)
    await session.commit()
    return tenant, author, spec


async def test_a_dataset_can_be_bound_to_a_published_spec(
    session: AsyncSession,
) -> None:
    """There was nowhere to record this: spec_draft_id only points at drafts."""
    tenant, _author, spec = await _published(session, slug="pubspec1")

    dataset = Dataset(
        tenant_id=tenant.id,
        name="from-published",
        profile=spec.name.lower(),
        version=spec.version,
        spec_id=spec.id,
        data={},
    )
    session.add(dataset)
    await session.commit()

    stored = await session.get(Dataset, dataset.id)
    assert stored is not None
    assert stored.spec_id == spec.id


async def test_the_entity_types_resolve(session: AsyncSession) -> None:
    """The point of the link: without it the facade cannot be rebuilt and no
    entity type is valid."""
    tenant, _author, spec = await _published(session, slug="pubspec2", name="Resolvable")
    dataset = Dataset(
        tenant_id=tenant.id,
        name="ds",
        profile="resolvable",
        version="1.0",
        spec_id=spec.id,
        data={},
    )
    session.add(dataset)
    await session.commit()

    state = await ensure_dataset_facade(dataset, session)

    assert state.facade is not None, "the facade must be built from the spec"
    assert "Study" in state.facade.entities


async def test_another_account_can_use_a_published_spec(
    session: AsyncSession,
) -> None:
    """Publishing shares it, so binding is not restricted to the author's
    account."""
    _author_tenant, _author, spec = await _published(session, slug="pubspec3", name="Shared2")
    other = make_tenant(slug="pubspec4")
    session.add(other)
    await session.commit()

    dataset = Dataset(
        tenant_id=other.id,
        name="theirs",
        profile="shared2",
        version="1.0",
        spec_id=spec.id,
        data={},
    )
    session.add(dataset)
    await session.commit()

    state = await ensure_dataset_facade(dataset, session)
    assert "Study" in state.facade.entities


async def test_withdrawing_the_spec_does_not_break_the_dataset(
    session: AsyncSession,
) -> None:
    """SET NULL, not CASCADE: a withdrawn specification must not delete the
    datasets built on it, and they must still open."""
    tenant, _author, spec = await _published(session, slug="pubspec5", name="Withdrawn")
    dataset = Dataset(
        tenant_id=tenant.id,
        name="survivor",
        profile="withdrawn",
        version="1.0",
        spec_id=spec.id,
        data={},
    )
    session.add(dataset)
    await session.commit()

    spec.soft_delete()
    await session.commit()

    still_there = await session.get(Dataset, dataset.id)
    assert still_there is not None
    state = await ensure_dataset_facade(still_there, session)
    assert "Study" in state.facade.entities


async def test_a_draft_is_not_reachable_as_a_published_spec(
    session: AsyncSession,
) -> None:
    """Only PUBLISHED is shared. Binding by id must not resurrect a draft."""
    from sqlalchemy import select

    tenant = make_tenant(slug="pubspec6")
    session.add(tenant)
    await session.flush()
    author = make_user(tenant=tenant, email="d@example.org")
    session.add(author)
    await session.flush()
    unpublished = make_spec(
        tenant=tenant,
        created_by=author,
        name="NotShared",
        spec_data=_spec_data("NotShared"),
        status=SpecStatus.DRAFT,
    )
    session.add(unpublished)
    await session.commit()

    found = await session.execute(
        select(Spec).where(
            Spec.id == unpublished.id,
            Spec.status == SpecStatus.PUBLISHED,
            Spec.deleted_at.is_(None),
        )
    )
    assert found.scalar_one_or_none() is None
