"""Which specifications are load-bearing, and which nothing uses.

The admin page counted how many specs each person had published. Nothing
showed the other direction — how many datasets *use* a given spec — which is
the number that answers whether one can be withdrawn. The relationship already
existed: refusing to delete a draft computes exactly these dependents.

Counted the same way that refusal counts them, so the page and the refusal
cannot disagree about what "in use" means: by `Dataset.spec_id` and
`Dataset.spec_draft_id`, ignoring soft-deleted datasets, and across every
tenant — a spec published in one account and used in another is in use.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import SpecDraft
from metaseed_hub.ui.routes.admin import (
    _dataset_counts_by_draft,
    _dataset_counts_by_spec,
)
from tests.factories import make_dataset, make_spec, make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _tenant_user(session: AsyncSession, slug: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=f"{slug}-kc")
    session.add(user)
    await session.flush()
    return tenant, user


async def test_a_spec_reports_the_datasets_that_use_it(session: AsyncSession) -> None:
    tenant, user = await _tenant_user(session, "usagecount")
    spec = make_spec(tenant=tenant, created_by=user)
    session.add(spec)
    await session.flush()
    for _ in range(2):
        dataset = make_dataset(tenant=tenant)
        dataset.spec_id = spec.id
        session.add(dataset)
    await session.commit()

    assert (await _dataset_counts_by_spec(session)).get(spec.id) == 2


async def test_a_deleted_dataset_does_not_keep_a_spec_looking_used(
    session: AsyncSession,
) -> None:
    """Otherwise a withdrawn spec reads as load-bearing forever."""
    tenant, user = await _tenant_user(session, "usagedeleted")
    spec = make_spec(tenant=tenant, created_by=user)
    session.add(spec)
    await session.flush()
    dataset = make_dataset(tenant=tenant)
    dataset.spec_id = spec.id
    session.add(dataset)
    await session.flush()
    dataset.soft_delete()
    await session.commit()

    assert (await _dataset_counts_by_spec(session)).get(spec.id, 0) == 0


async def test_a_spec_used_from_another_tenant_counts(session: AsyncSession) -> None:
    """The admin view spans tenants; cross-account use is still use."""
    home, author = await _tenant_user(session, "usagehome")
    away, _borrower = await _tenant_user(session, "usageaway")
    spec = make_spec(tenant=home, created_by=author)
    session.add(spec)
    await session.flush()
    dataset = make_dataset(tenant=away)
    dataset.spec_id = spec.id
    session.add(dataset)
    await session.commit()

    assert (await _dataset_counts_by_spec(session)).get(spec.id) == 1


async def test_a_draft_reports_the_datasets_built_on_it(session: AsyncSession) -> None:
    tenant, user = await _tenant_user(session, "usagedraft")
    draft = SpecDraft(
        tenant_id=tenant.id,
        user_id=user.id,
        name="Fieldbook",
        version="1.0",
        spec_data={},
    )
    session.add(draft)
    await session.flush()
    dataset = make_dataset(tenant=tenant)
    dataset.spec_draft_id = draft.id
    session.add(dataset)
    await session.commit()

    assert (await _dataset_counts_by_draft(session)).get(draft.id) == 1


async def test_a_spec_nothing_uses_is_absent_rather_than_wrong(
    session: AsyncSession,
) -> None:
    """An unused spec must read as zero, which the template renders from a default."""
    tenant, user = await _tenant_user(session, "usageunused")
    spec = make_spec(tenant=tenant, created_by=user)
    session.add(spec)
    await session.commit()

    assert (await _dataset_counts_by_spec(session)).get(spec.id, 0) == 0


async def test_the_dashboard_shows_the_count(session: AsyncSession) -> None:
    """A number nobody can see answers nothing: the page must render it."""
    from unittest.mock import Mock

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.ui.app import create_hub_app
    from metaseed_hub.ui.routes.admin import admin_dashboard

    # Building the app is what registers the template environment the shared
    # renderer uses; without it the route raises before rendering anything.
    create_hub_app()

    tenant, user = await _tenant_user(session, "usagepage")
    spec = make_spec(tenant=tenant, created_by=user, name="Loadbearing")
    session.add(spec)
    await session.flush()
    dataset = make_dataset(tenant=tenant)
    dataset.spec_id = spec.id
    session.add(dataset)
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

    body = response.body.decode()
    assert "Published Specifications" in body
    assert "Loadbearing" in body


async def test_the_errors_live_in_their_own_tab(session: AsyncSession) -> None:
    """Errors are noisy and rarely why an admin opened the page.

    They sat between the tables people actually use, and an exception message
    or a path with a UUID in it is arbitrarily long, so the row grew until the
    table pushed past the page.
    """
    from unittest.mock import Mock

    from metaseed_hub.auth import TokenUser
    from metaseed_hub.ui.app import create_hub_app
    from metaseed_hub.ui.routes.admin import admin_dashboard

    create_hub_app()
    request = Mock()
    request.url.path = "/hub/admin/"
    request.headers = {}
    request.cookies = {}
    response = await admin_dashboard(
        request=request,
        session=session,
        user=TokenUser(sub="admin-kc", email="a@example.org", name="A", roles=["admin"]),
    )

    body = response.body.decode()
    assert 'id="panel-errors"' in body, "errors must have their own panel"
    assert 'id="panel-overview"' in body
    assert 'data-tab="errors"' in body, "and a tab that reaches it"

    # The errors heading belongs inside the errors panel, not the overview one.
    overview = body.split('id="panel-overview"')[1].split('id="panel-errors"')[0]
    assert "<h2>Errors</h2>" not in overview
