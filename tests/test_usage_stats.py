"""The aggregate usage figures on the admin dashboard.

Each test builds a small deployment whose correct figures are known by
construction and checks the exact numbers, including the rows that must stay
at zero: a figure that counts the wrong thing still produces a number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import (
    DatasetCollaborationGrant,
    DatasetMember,
    DatasetVersion,
    SpecDraftCollaborationGrant,
    SpecDraftMember,
    SpecMember,
    Tenant,
    User,
)
from metaseed_hub.sharing import Role
from metaseed_hub.usage_stats import (
    active_users,
    dataset_counts,
    share_counts,
    shares_received_by_user,
    spec_counts,
    users_by_month,
)
from tests.factories import make_dataset, make_spec, make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
TEAM = "urn:mace:surf.nl:sram:group:tudelft:cropxr"
LAB = "urn:mace:surf.nl:sram:group:tudelft:cropxr:lab"


async def _person(session: AsyncSession, label: str, **fields: object) -> tuple[User, Tenant]:
    """A user in a tenant of their own, with any column set from ``fields``."""
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=f"{label}@example.org")
    for column, value in fields.items():
        setattr(user, column, value)
    session.add(user)
    await session.flush()
    return user, tenant


async def test_specifications_are_counted_by_audience_and_sharing(session: AsyncSession) -> None:
    owner, owner_tenant = await _person(session, "owner")
    reader, _ = await _person(session, "reader")
    tenant = owner_tenant
    session.add(make_spec(tenant=tenant, created_by=owner, name="open"))
    restricted = make_spec(tenant=tenant, created_by=owner, name="restricted")
    restricted.audience_urn = TEAM
    session.add(restricted)
    withdrawn = make_spec(tenant=tenant, created_by=owner, name="gone")
    withdrawn.deleted_at = NOW
    session.add(withdrawn)
    alone = make_spec_draft(tenant=tenant, user=owner, name="alone")
    to_person = make_spec_draft(tenant=tenant, user=owner, name="to-person")
    to_team = make_spec_draft(tenant=tenant, user=owner, name="to-team")
    session.add_all([alone, to_person, to_team])
    await session.flush()
    session.add(SpecDraftMember(spec_draft_id=to_person.id, user_id=reader.id, role=Role.VIEWER))
    session.add(SpecDraftMember(spec_draft_id=to_team.id, user_id=owner.id, role=Role.OWNER))
    session.add(SpecDraftCollaborationGrant(spec_draft_id=to_team.id, urn=TEAM, role=Role.VIEWER))
    await session.commit()

    counts = await spec_counts(session, builtin=7)

    assert counts.builtin == 7
    assert counts.published_to_everyone == 1
    assert counts.published_to_a_collaboration == 1
    assert counts.drafts == 3
    assert counts.drafts_shared_with_individuals == 1
    assert counts.drafts_shared_with_a_collaboration == 1


async def test_datasets_are_counted_by_sharing_and_profile_source(session: AsyncSession) -> None:
    owner, owner_tenant = await _person(session, "owner")
    reader, _ = await _person(session, "reader")
    tenant = owner_tenant
    spec = make_spec(tenant=tenant, created_by=owner)
    draft = make_spec_draft(tenant=tenant, user=owner)
    session.add_all([spec, draft])
    await session.flush()
    private = make_dataset(tenant=tenant, name="private")
    to_person = make_dataset(tenant=tenant, name="to-person")
    to_person.spec_id = spec.id
    to_both = make_dataset(tenant=tenant, name="to-both")
    to_both.spec_draft_id = draft.id
    deleted = make_dataset(tenant=tenant, name="deleted")
    deleted.deleted_at = NOW
    session.add_all([private, to_person, to_both, deleted])
    await session.flush()
    session.add(DatasetMember(dataset_id=private.id, user_id=owner.id, role=Role.OWNER))
    session.add(DatasetMember(dataset_id=to_person.id, user_id=reader.id, role=Role.EDITOR))
    session.add(DatasetMember(dataset_id=to_both.id, user_id=reader.id, role=Role.VIEWER))
    session.add(DatasetCollaborationGrant(dataset_id=to_both.id, urn=TEAM, role=Role.VIEWER))
    session.add(DatasetCollaborationGrant(dataset_id=deleted.id, urn=TEAM, role=Role.VIEWER))
    await session.commit()

    counts = await dataset_counts(session)

    assert counts.total == 3
    assert counts.private == 1
    assert counts.shared_with_individuals == 2
    assert counts.shared_with_a_collaboration == 1
    assert counts.on_builtin_profile == 1
    assert counts.on_published_spec == 1
    assert counts.on_draft == 1


async def test_shares_exclude_owners_and_are_grouped_by_collaboration(
    session: AsyncSession,
) -> None:
    owner, owner_tenant = await _person(session, "owner")
    reader, _ = await _person(session, "reader")
    tenant = owner_tenant
    dataset = make_dataset(tenant=tenant)
    spec = make_spec(tenant=tenant, created_by=owner)
    draft = make_spec_draft(tenant=tenant, user=owner)
    session.add_all([dataset, spec, draft])
    await session.flush()
    session.add_all(
        [
            DatasetMember(dataset_id=dataset.id, user_id=owner.id, role=Role.OWNER),
            DatasetMember(dataset_id=dataset.id, user_id=reader.id, role=Role.VIEWER),
            SpecMember(spec_id=spec.id, user_id=reader.id, role=Role.EDITOR),
            SpecDraftMember(spec_draft_id=draft.id, user_id=reader.id, role=Role.VIEWER),
            DatasetCollaborationGrant(dataset_id=dataset.id, urn=TEAM, role=Role.VIEWER),
            SpecDraftCollaborationGrant(spec_draft_id=draft.id, urn=TEAM, role=Role.EDITOR),
            SpecDraftCollaborationGrant(spec_draft_id=draft.id, urn=LAB, role=Role.VIEWER),
        ]
    )
    await session.commit()

    shares = await share_counts(session)

    assert shares.individual == 3
    assert shares.collaboration == 3
    assert shares.by_collaboration == (("cropxr", 2), ("cropxr / lab", 1))
    assert await shares_received_by_user(session) == {reader.id: 3}


async def test_users_are_counted_cumulatively_per_month(session: AsyncSession) -> None:
    await _person(session, "early", created_at=datetime(2026, 8, 3, tzinfo=UTC))
    await _person(session, "middle", created_at=datetime(2026, 10, 20, tzinfo=UTC))
    await _person(session, "late", created_at=datetime(2026, 10, 30, tzinfo=UTC))
    await _person(
        session,
        "removed",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        deleted_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    await session.commit()

    assert await users_by_month(session, now=NOW) == [
        ("2026-08", 1),
        ("2026-09", 1),
        ("2026-10", 3),
        ("2026-11", 3),
    ]


async def test_active_users_are_those_who_signed_in_or_saved_in_30_days(
    session: AsyncSession,
) -> None:
    recent, _ = await _person(session, "recent", last_login_at=NOW - timedelta(days=3))
    await _person(session, "lapsed", last_login_at=NOW - timedelta(days=40))
    saver, saver_tenant = await _person(session, "saver", last_login_at=NOW - timedelta(days=90))
    dataset = make_dataset(tenant=saver_tenant)
    session.add(dataset)
    await session.flush()
    for number in (1, 2):
        session.add(
            DatasetVersion(
                dataset_id=dataset.id,
                version_number=number,
                data={},
                created_by_id=saver.id,
                created_at=NOW - timedelta(days=number),
            )
        )
    session.add(
        DatasetVersion(
            dataset_id=dataset.id,
            version_number=3,
            data={},
            created_by_id=recent.id,
            created_at=NOW - timedelta(days=45),
        )
    )
    await session.commit()

    active = await active_users(session, now=NOW)

    assert active.signed_in == 1
    assert active.saved_a_dataset == 1


async def test_the_dashboard_shows_the_figures(session: AsyncSession) -> None:
    """A figure nobody can see answers nothing: the page must render each one."""
    import re
    from unittest.mock import Mock

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.ui.app import create_hub_app
    from metaseed_hub.ui.routes.admin import admin_dashboard

    create_hub_app()
    owner, owner_tenant = await _person(session, "owner")
    reader, _ = await _person(session, "reader")
    dataset = make_dataset(tenant=owner_tenant)
    session.add(dataset)
    await session.flush()
    session.add(DatasetMember(dataset_id=dataset.id, user_id=reader.id, role=Role.VIEWER))
    session.add(DatasetCollaborationGrant(dataset_id=dataset.id, urn=TEAM, role=Role.VIEWER))
    await session.commit()

    request = Mock()
    request.url.path = "/hub/admin/"
    request.headers = {}
    request.cookies = {}
    response = await admin_dashboard(
        request=request,
        session=session,
        user=TokenUser(sub="admin-kc", email="a@example.org", name="A", roles=["admin"]),
    )
    body = re.sub(r"\s+", " ", response.body.decode())

    for section in ("active-users", "content-counts", "sharing", "users-over-time"):
        assert f'data-testid="admin-{section}"' in body
    assert "<td>Shared with a collaboration</td><td>1</td>" in body
    assert "<td>cropxr</td><td>1</td>" in body
    assert "Shared with them" in body
