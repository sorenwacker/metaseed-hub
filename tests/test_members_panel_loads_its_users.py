"""The members panel must not lazy-load users mid-render (260817 review).

`members_of` joins User for filtering but selects only the membership rows, and
`DatasetMember.user` is a default-lazy relationship. The panel template reads
`member.user.email` for every row, so on an AsyncSession that is a sync lazy
load — `MissingGreenlet`, a 500.

It looks fine in tests today only because they create every User in the same
session, so the rows are already in the identity map. This test uses a SECOND
session, as an HTTP request does: the request that renders the panel is not the
one that created the members.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import DatasetMember, DatasetRole
from metaseed_hub.sharing import members_of, resource_for
from metaseed_hub.ui.dependencies import tenant_slug_for
from tests.factories import make_dataset, make_tenant, make_user


@pytest.mark.asyncio
async def test_a_member_renders_without_a_lazy_load(session: AsyncSession) -> None:
    tenant = make_tenant(slug=tenant_slug_for("panel-kc"))
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant, keycloak_id="panel-owner", email="o@example.org")
    other = make_user(tenant=tenant, keycloak_id="panel-other", email="x@example.org")
    session.add_all([owner, other])
    await session.flush()
    dataset = make_dataset(tenant=tenant, name="panel-probe")
    session.add(dataset)
    await session.flush()
    session.add_all(
        [
            DatasetMember(dataset_id=dataset.id, user_id=owner.id, role=DatasetRole.OWNER),
            DatasetMember(dataset_id=dataset.id, user_id=other.id, role=DatasetRole.VIEWER),
        ]
    )
    await session.commit()

    # A fresh session: the identity map is empty, exactly as in the GET request
    # that renders the panel.
    session.expunge_all()

    members = await members_of(session, resource_for("dataset"), dataset.id)

    # What the template does for every row.
    emails = sorted(m.user.email for m in members)

    assert emails == ["o@example.org", "x@example.org"]
