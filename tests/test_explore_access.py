"""Tests that explore profile loading is scoped to the caller's tenant.

``load_profile_spec`` resolves drafts and published specs by id. Without tenant
scoping a caller could load specs belonging to another tenant by supplying the
id. These tests verify that database-backed specs are only returned for the
caller's own tenant and that soft-deleted published specs are excluded.
"""

import pytest
from metaseed.specs.schema import ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.explore_routes import load_profile_spec
from tests.factories import make_spec, make_spec_draft, make_tenant, make_user


def _spec_data() -> dict:
    """A minimal, valid serialized ProfileSpec."""
    return ProfileSpec(name="T", version="1.0").model_dump(mode="json")


@pytest.mark.asyncio
async def test_draft_loads_for_own_tenant(session: AsyncSession) -> None:
    """A draft is returned when the caller's tenant owns it."""
    tenant = make_tenant(slug="own12345")
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant)
    session.add(user)
    await session.flush()
    draft = make_spec_draft(tenant=tenant, user=user, spec_data=_spec_data())
    session.add(draft)
    await session.commit()

    loaded = await load_profile_spec(session, f"draft:{draft.id}", "1.0", tenant.id)

    assert loaded is not None
    assert "Draft" in loaded[0]


@pytest.mark.asyncio
async def test_draft_not_loaded_for_other_tenant(session: AsyncSession) -> None:
    """A draft owned by another tenant is not returned."""
    owner_tenant = make_tenant(slug="owner123")
    other_tenant = make_tenant(slug="other123")
    session.add_all([owner_tenant, other_tenant])
    await session.flush()
    owner = make_user(tenant=owner_tenant)
    session.add(owner)
    await session.flush()
    draft = make_spec_draft(tenant=owner_tenant, user=owner, spec_data=_spec_data())
    session.add(draft)
    await session.commit()

    loaded = await load_profile_spec(session, f"draft:{draft.id}", "1.0", other_tenant.id)

    assert loaded is None


@pytest.mark.asyncio
async def test_a_published_spec_loads_for_any_tenant(session: AsyncSession) -> None:
    """Publishing shares a specification with everyone on the platform.

    This assertion was previously the opposite: a published spec was not
    returned outside its own account. That made publishing unobservable to
    anyone but its author, which was never the intent — a draft is the private
    form, and publishing is what makes a specification available to others.
    Drafts remain scoped, which the tests above cover.
    """
    owner_tenant = make_tenant(slug="owner456")
    other_tenant = make_tenant(slug="other456")
    session.add_all([owner_tenant, other_tenant])
    await session.flush()
    owner = make_user(tenant=owner_tenant)
    session.add(owner)
    await session.flush()
    spec = make_spec(tenant=owner_tenant, created_by=owner, spec_data=_spec_data())
    session.add(spec)
    await session.commit()

    loaded = await load_profile_spec(session, f"spec:{spec.id}", "1.0", other_tenant.id)

    assert loaded is not None
    assert "Published" in loaded[0]


@pytest.mark.asyncio
async def test_an_unpublished_spec_is_not_loadable_by_id(session: AsyncSession) -> None:
    """Only PUBLISHED status is shared. A spec in any other state stays private
    even though the lookup is no longer scoped by account."""
    from metaseed_hub.models import SpecStatus

    owner_tenant = make_tenant(slug="owner457")
    other_tenant = make_tenant(slug="other457")
    session.add_all([owner_tenant, other_tenant])
    await session.flush()
    owner = make_user(tenant=owner_tenant)
    session.add(owner)
    await session.flush()
    spec = make_spec(
        tenant=owner_tenant,
        created_by=owner,
        spec_data=_spec_data(),
        status=SpecStatus.DRAFT,
    )
    session.add(spec)
    await session.commit()

    assert await load_profile_spec(session, f"spec:{spec.id}", "1.0", other_tenant.id) is None


@pytest.mark.asyncio
async def test_soft_deleted_spec_not_loaded(session: AsyncSession) -> None:
    """A soft-deleted published spec is not returned for its own tenant."""
    tenant = make_tenant(slug="own45678")
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant)
    session.add(user)
    await session.flush()
    spec = make_spec(tenant=tenant, created_by=user, spec_data=_spec_data())
    session.add(spec)
    await session.flush()
    spec.soft_delete()
    await session.commit()

    loaded = await load_profile_spec(session, f"spec:{spec.id}", "1.0", tenant.id)

    assert loaded is None


@pytest.mark.asyncio
async def test_catalog_offers_published_specs_without_tenant_row(
    session: AsyncSession,
) -> None:
    """The explorer catalog lists published specs even for a caller whose
    Tenant row does not exist yet.

    The published-specs query was previously nested under the tenant lookup,
    so the catalog depended on whether the user had visited a page that runs
    ensure_tenant_and_user first.
    """
    from metaseed_hub.auth import TokenUser
    from metaseed_hub.ui.explore_routes import _build_explore_catalog

    owner_tenant = make_tenant(slug="owner789")
    session.add(owner_tenant)
    await session.flush()
    owner = make_user(tenant=owner_tenant)
    session.add(owner)
    await session.flush()
    spec = make_spec(tenant=owner_tenant, created_by=owner, spec_data=_spec_data())
    session.add(spec)
    await session.commit()

    caller = TokenUser(sub="fresh-user-no-tenant", email="f@example.org", name="F", roles=[])
    profiles, profile_versions, display_names = await _build_explore_catalog(session, caller)

    assert f"spec:{spec.id}" in profiles
    assert profile_versions[f"spec:{spec.id}"] == [spec.version]
    assert "Published" in display_names[f"spec:{spec.id}"]


@pytest.mark.asyncio
async def test_no_tenant_blocks_database_specs(session: AsyncSession) -> None:
    """When the caller has no tenant, database specs are inaccessible."""
    tenant = make_tenant(slug="own99999")
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant)
    session.add(user)
    await session.flush()
    draft = make_spec_draft(tenant=tenant, user=user, spec_data=_spec_data())
    session.add(draft)
    await session.commit()

    loaded = await load_profile_spec(session, f"draft:{draft.id}", "1.0", None)

    assert loaded is None


@pytest.mark.asyncio
async def test_a_shared_draft_loads_for_its_member(session: AsyncSession) -> None:
    """The catalog offers drafts shared via SpecDraftMember; loading must too.

    _build_explore_catalog lists drafts the user is a member of across
    tenants, but load_profile_spec accepted only the caller's own tenant —
    so a shared draft appeared in the picker and then refused to compare.
    """
    from metaseed_hub.models import Role, SpecDraftMember

    owner_tenant = make_tenant(slug="shr00001")
    member_tenant = make_tenant(slug="shr00002")
    session.add_all([owner_tenant, member_tenant])
    await session.flush()
    owner = make_user(tenant=owner_tenant)
    member = make_user(tenant=member_tenant, email="member@example.org")
    session.add_all([owner, member])
    await session.flush()
    draft = make_spec_draft(tenant=owner_tenant, user=owner, spec_data=_spec_data())
    session.add(draft)
    await session.flush()
    session.add(SpecDraftMember(spec_draft_id=draft.id, user_id=member.id, role=Role.VIEWER))
    await session.commit()

    loaded = await load_profile_spec(
        session, f"draft:{draft.id}", "1.0", member_tenant.id, user_id=member.id
    )

    assert loaded is not None
