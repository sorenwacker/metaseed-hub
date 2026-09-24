"""SRAM collaborations: the membership snapshot, the people in one, and grants.

The identity provider states group membership at sign-in and nowhere else. The
hub records that statement per user, and everything here reads the record:
who is in a collaboration, and which items a collaboration grant reaches.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.collaborations import (
    Collaboration,
    NotInCollaborationError,
    collaborations_of,
    entitled_urns_of,
    grant_label,
    people_in,
    record_memberships,
)
from metaseed_hub.entitlements import entitled_urns
from metaseed_hub.models import SpecDraft
from metaseed_hub.sharing import (
    NotAnOwnerError,
    Role,
    SharingError,
    accessible_ids,
    add_grant,
    add_member,
    grants_of,
    record_creator,
    remove_grant,
    resource_for,
    role_of,
    set_grant_role,
)
from tests.factories import make_dataset, make_spec, make_tenant, make_user

pytestmark = pytest.mark.asyncio

PREFIX = "urn:mace:surf.nl:sram:group:"
PHENO = PREFIX + "tudelft:cropxr:phenotyping"
SEQ = PREFIX + "tudelft:cropxr:sequencing"
OTHER = PREFIX + "tudelft:other:members"
CROPXR = PREFIX + "tudelft:cropxr"
NOT_A_GROUP = "urn:mace:surf.nl:sram:label:something"

KINDS = ["dataset", "draft", "spec"]

SPEC_PAYLOAD = {
    "spec": {
        "name": "shared",
        "version": "1.0",
        "root_entity": "Sample",
        "entities": {"Sample": {"description": "a sample", "fields": []}},
    }
}


async def _person(session: AsyncSession, slug: str, email: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=f"kc-{slug}", email=email, display_name=slug)
    session.add(user)
    await session.flush()
    return tenant, user


@pytest.fixture
async def people(session: AsyncSession):
    """An owner, a colleague in the same collaboration, and a stranger."""
    owner = await _person(session, "owner", "owner@example.org")
    colleague = await _person(session, "colleague", "colleague@example.org")
    stranger = await _person(session, "stranger", "stranger@example.org")
    await record_memberships(session, owner[1].id, [PHENO, SEQ])
    await record_memberships(session, colleague[1].id, [PHENO])
    await record_memberships(session, stranger[1].id, [OTHER])
    await session.commit()
    return owner, colleague, stranger


async def _make(session: AsyncSession, kind: str, tenant, user) -> str:
    name = f"shared-{uuid4().hex[:6]}"
    if kind == "dataset":
        thing = make_dataset(tenant=tenant, name=name)
    elif kind == "draft":
        thing = SpecDraft(
            tenant_id=tenant.id, user_id=user.id, name=name, version="1.0", spec_data=SPEC_PAYLOAD
        )
    else:
        thing = make_spec(
            tenant=tenant, created_by=user, name=name, version="1.0", spec_data=SPEC_PAYLOAD
        )
    session.add(thing)
    await record_creator(session, resource_for(kind), thing, user.id)
    await session.commit()
    return str(thing.id)


# --- the snapshot ---------------------------------------------------------


async def test_a_sign_in_replaces_the_previous_snapshot(session: AsyncSession) -> None:
    _, user = await _person(session, "p1", "p1@example.org")
    await record_memberships(session, user.id, [PHENO, OTHER, NOT_A_GROUP])
    await record_memberships(session, user.id, [SEQ, PHENO])
    await session.commit()

    assert await entitled_urns_of(session, user.id) == {PHENO, SEQ, CROPXR}


async def test_collaborations_group_the_snapshot_by_collaboration(session: AsyncSession) -> None:
    _, user = await _person(session, "p2", "p2@example.org")
    await record_memberships(session, user.id, [SEQ, PHENO, OTHER])
    await session.commit()

    found = await collaborations_of(session, user.id)

    assert [c.urn for c in found] == [CROPXR, PREFIX + "tudelft:other"]
    assert found[0] == Collaboration(
        urn=CROPXR,
        organisation="tudelft",
        name="cropxr",
        groups=["phenotyping", "sequencing"],
        read_at=found[0].read_at,
    )


async def test_a_stale_snapshot_grants_nothing(session: AsyncSession) -> None:
    _, user = await _person(session, "p3", "p3@example.org")
    long_ago = datetime.now(UTC) - timedelta(days=31)
    await record_memberships(session, user.id, [PHENO], seen_at=long_ago)
    await session.commit()

    assert await entitled_urns_of(session, user.id) == set()
    assert await collaborations_of(session, user.id) == []


# --- the people -----------------------------------------------------------


async def test_people_in_a_collaboration_are_its_signed_in_members(
    session: AsyncSession, people
) -> None:
    (_, owner), (_, colleague), _ = people

    found = await people_in(session, CROPXR, viewer_id=owner.id)

    assert [p.email for p in found] == ["colleague@example.org", "owner@example.org"]
    assert found[0].display_name == "colleague"


async def test_a_deleted_or_stale_member_is_not_listed(session: AsyncSession, people) -> None:
    (_, owner), (_, colleague), _ = people
    colleague.soft_delete()
    _, late = await _person(session, "late", "late@example.org")
    await record_memberships(
        session, late.id, [SEQ], seen_at=datetime.now(UTC) - timedelta(days=31)
    )
    await session.commit()

    found = await people_in(session, CROPXR, viewer_id=owner.id)

    assert [p.email for p in found] == ["owner@example.org"]


async def test_only_members_may_list_a_collaboration(session: AsyncSession, people) -> None:
    _, _, (_, stranger) = people

    with pytest.raises(NotInCollaborationError):
        await people_in(session, CROPXR, viewer_id=stranger.id)


# --- grants ---------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
async def test_a_grant_gives_every_member_its_role(session: AsyncSession, people, kind) -> None:
    (tenant, owner), (_, colleague), (_, stranger) = people
    resource = resource_for(kind)
    thing_id = await _make(session, kind, tenant, owner)

    await add_grant(session, resource, thing_id, actor_id=owner.id, urn=CROPXR, role=Role.EDITOR)

    assert await role_of(session, resource, thing_id, colleague.id) is Role.EDITOR
    assert await role_of(session, resource, thing_id, stranger.id) is None
    assert await role_of(session, resource, thing_id, owner.id) is Role.OWNER


@pytest.mark.parametrize("kind", KINDS)
async def test_an_explicit_membership_beats_a_grant(session: AsyncSession, people, kind) -> None:
    (tenant, owner), (_, colleague), _ = people
    resource = resource_for(kind)
    thing_id = await _make(session, kind, tenant, owner)
    await add_grant(session, resource, thing_id, actor_id=owner.id, urn=CROPXR, role=Role.EDITOR)
    await add_member(
        session, resource, thing_id, actor_id=owner.id, email=colleague.email, role=Role.VIEWER
    )

    assert await role_of(session, resource, thing_id, colleague.id) is Role.VIEWER


async def test_a_grant_can_name_a_group_rather_than_the_whole_collaboration(
    session: AsyncSession, people
) -> None:
    (tenant, owner), (_, colleague), _ = people
    resource = resource_for("dataset")
    thing_id = await _make(session, "dataset", tenant, owner)

    await add_grant(session, resource, thing_id, actor_id=owner.id, urn=SEQ, role=Role.VIEWER)

    # The colleague is in phenotyping only.
    assert await role_of(session, resource, thing_id, colleague.id) is None


async def test_a_grant_stops_reaching_a_member_whose_snapshot_went_stale(
    session: AsyncSession, people
) -> None:
    (tenant, owner), (_, colleague), _ = people
    resource = resource_for("dataset")
    thing_id = await _make(session, "dataset", tenant, owner)
    await add_grant(session, resource, thing_id, actor_id=owner.id, urn=CROPXR, role=Role.VIEWER)
    await record_memberships(
        session, colleague.id, [PHENO], seen_at=datetime.now(UTC) - timedelta(days=31)
    )
    await session.commit()

    assert await role_of(session, resource, thing_id, colleague.id) is None


async def test_owner_cannot_be_granted_to_a_collaboration(session: AsyncSession, people) -> None:
    (tenant, owner), _, _ = people
    resource = resource_for("dataset")
    thing_id = await _make(session, "dataset", tenant, owner)

    with pytest.raises(SharingError):
        await add_grant(session, resource, thing_id, actor_id=owner.id, urn=CROPXR, role=Role.OWNER)
    await add_grant(session, resource, thing_id, actor_id=owner.id, urn=CROPXR, role=Role.VIEWER)
    grant = (await grants_of(session, resource, thing_id))[0]
    with pytest.raises(SharingError):
        await set_grant_role(
            session, resource, thing_id, actor_id=owner.id, grant_id=grant.id, role=Role.OWNER
        )


async def test_only_an_owner_in_the_collaboration_may_grant(session: AsyncSession, people) -> None:
    (tenant, owner), (_, colleague), (_, stranger) = people
    resource = resource_for("dataset")
    thing_id = await _make(session, "dataset", tenant, owner)

    with pytest.raises(NotAnOwnerError):
        await add_grant(
            session, resource, thing_id, actor_id=colleague.id, urn=CROPXR, role=Role.VIEWER
        )
    # The owner is not in "other", so cannot hand the dataset to it.
    with pytest.raises(NotInCollaborationError):
        await add_grant(
            session,
            resource,
            thing_id,
            actor_id=owner.id,
            urn=PREFIX + "tudelft:other",
            role=Role.VIEWER,
        )
    assert await role_of(session, resource, thing_id, stranger.id) is None


async def test_a_grant_can_be_changed_and_removed(session: AsyncSession, people) -> None:
    (tenant, owner), (_, colleague), _ = people
    resource = resource_for("dataset")
    thing_id = await _make(session, "dataset", tenant, owner)
    await add_grant(session, resource, thing_id, actor_id=owner.id, urn=CROPXR, role=Role.VIEWER)
    # Granting again is a role change, not a second row.
    await add_grant(session, resource, thing_id, actor_id=owner.id, urn=CROPXR, role=Role.EDITOR)
    grants = await grants_of(session, resource, thing_id)
    assert [(g.urn, g.role) for g in grants] == [(CROPXR, Role.EDITOR)]

    await set_grant_role(
        session, resource, thing_id, actor_id=owner.id, grant_id=grants[0].id, role=Role.VIEWER
    )
    assert await role_of(session, resource, thing_id, colleague.id) is Role.VIEWER

    await remove_grant(session, resource, thing_id, actor_id=owner.id, grant_id=grants[0].id)
    assert await grants_of(session, resource, thing_id) == []
    assert await role_of(session, resource, thing_id, colleague.id) is None


@pytest.mark.parametrize("kind", KINDS)
async def test_lists_reach_items_shared_through_a_grant(
    session: AsyncSession, people, kind
) -> None:
    (tenant, owner), (_, colleague), (_, stranger) = people
    resource = resource_for(kind)
    granted = await _make(session, kind, tenant, owner)
    private = await _make(session, kind, tenant, owner)
    await add_grant(session, resource, granted, actor_id=owner.id, urn=CROPXR, role=Role.VIEWER)
    await add_member(
        session, resource, private, actor_id=owner.id, email=stranger.email, role=Role.VIEWER
    )

    assert await accessible_ids(session, resource, colleague.id) == {granted}
    assert await accessible_ids(session, resource, stranger.id) == {private}


async def test_the_snapshot_and_a_live_token_yield_the_same_urns(session: AsyncSession) -> None:
    """One implementation, read twice. The snapshot reader re-derived the
    collaboration URN with its own loop, so the two could drift apart while
    both looked right."""
    _, user = await _person(session, "p4", "p4@example.org")
    reported = [PHENO, SEQ, OTHER, NOT_A_GROUP]
    await record_memberships(session, user.id, reported)
    await session.commit()

    assert await entitled_urns_of(session, user.id) == entitled_urns(reported)


async def test_a_grant_is_labelled_by_its_collaboration_not_its_urn() -> None:
    assert grant_label(CROPXR) == "cropxr"
    assert grant_label(PHENO) == "cropxr / phenotyping"
