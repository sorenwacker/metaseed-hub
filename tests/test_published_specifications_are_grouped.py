"""The published list groups the versions of one specification, as the drafts do.

Four releases of ``imaging`` were four cards with the same title, author and
near-identical description, and only a version number to tell them apart. A
published specification is grouped within the account it lives in, since two
accounts may each release a specification of the same name.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.audience import EVERYONE
from metaseed_hub.collaborations import record_memberships
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.spec_builder.grouping import group_published
from tests.factories import make_spec, make_tenant, make_user
from tests.test_publishing_to_a_collaboration import CROPXR, PHENO, _page

pytestmark = pytest.mark.asyncio

TEMPLATE = Path("src/metaseed_hub/ui/templates/spec_builder/list.html")


def _payload(name: str, version: str) -> dict:
    return {
        "spec": {
            "name": name,
            "display_name": name,
            "version": version,
            "root_entity": "Sample",
            "entities": {"Sample": {"description": "a sample", "fields": []}},
        }
    }


async def _person(session: AsyncSession, slug: str):
    sub = f"{slug}-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(user)
    await session.flush()
    await record_memberships(session, user.id, [PHENO])
    await session.commit()
    return tenant, user


async def _released(session, tenant, user, name: str, version: str, audience=EVERYONE):
    spec = make_spec(
        tenant=tenant,
        created_by=user,
        name=name,
        version=version,
        description=f"Generated from {name} {version}.0",
        spec_data=_payload(name, version),
    )
    spec.audience_urn = audience
    session.add(spec)
    await session.commit()
    return spec


async def test_one_group_per_specification_per_account(session: AsyncSession) -> None:
    tenant, author = await _person(session, "grp-author")
    other_tenant, other = await _person(session, "grp-other")
    for version in ("0.1", "1.1", "0.2", "1.0"):
        await _released(session, tenant, author, "imaging", version)
    await _released(session, other_tenant, other, "imaging", "1.0")

    groups = group_published(
        [*await _specs_of(session, tenant), *await _specs_of(session, other_tenant)]
    )

    assert len(groups) == 2
    mine = next(g for g in groups if g.versions[0].tenant_id == tenant.id)
    assert [s.version for s in mine.versions] == ["1.1", "1.0", "0.2", "0.1"]
    assert mine.name == "imaging"
    assert mine.one_audience, "every version went to everyone"
    assert mine.audience_urn == EVERYONE


async def test_a_mixed_audience_is_reported_per_version(session: AsyncSession) -> None:
    tenant, author = await _person(session, "grp-mixed")
    await _released(session, tenant, author, "cinema", "1.0", audience=CROPXR)
    await _released(session, tenant, author, "cinema", "1.1", audience=EVERYONE)

    (group,) = group_published(await _specs_of(session, tenant))

    assert not group.one_audience, "no single audience to put on the card"


def test_drafts_and_releases_share_the_grouping() -> None:
    """One rule for both lists, so the two cannot drift apart."""
    import inspect

    assert "group_by_specification" in inspect.getsource(group_published)


async def test_the_specs_page_has_one_card_with_a_row_per_version(
    session: AsyncSession, app_db
) -> None:
    tenant, author = await _person(session, "grp-page")
    for version in ("0.1", "1.0", "1.1"):
        await _released(session, tenant, author, "imaging", version)

    html = _page("/hub/spec-builder", author.keycloak_id)

    assert html.count('data-testid="published-group-imaging"') == 1
    for version in ("0.1", "1.0", "1.1"):
        assert f'data-testid="published-version-imaging-{version}"' in html
    assert "3 versions" in html
    # The card describes the newest version; the older descriptions are on
    # their own pages.
    assert "Generated from imaging 1.1.0" in html
    assert "Generated from imaging 0.1.0" not in html
    assert html.index("published-version-imaging-1.1") < html.index("published-version-imaging-0.1")
    # One audience badge for the card, not one per row.
    assert html.count('data-testid="audience-imaging"') == 1


async def test_a_mixed_audience_puts_the_badge_on_the_rows(session: AsyncSession, app_db) -> None:
    tenant, author = await _person(session, "grp-rows")
    await _released(session, tenant, author, "cinema", "1.0", audience=CROPXR)
    await _released(session, tenant, author, "cinema", "1.1", audience=EVERYONE)

    html = _page("/hub/spec-builder", author.keycloak_id)

    assert 'data-testid="audience-cinema"' not in html
    assert 'data-testid="audience-cinema-1.0"' in html
    assert 'data-testid="audience-cinema-1.1"' in html


def test_every_version_row_keeps_its_own_buttons() -> None:
    """View and Fork act on one version, so they sit on the row, not the card."""
    text = TEMPLATE.read_text()
    published = text[text.index("Published Specifications") :]
    row = published[published.index("published-version-") :]
    assert "/hub/spec-builder/spec/{{ spec.id }}" in row
    assert "/hub/spec-builder/spec/{{ spec.id }}/edit" in row


async def _specs_of(session: AsyncSession, tenant):
    from sqlalchemy import select

    from metaseed_hub.models import Spec

    rows = await session.execute(select(Spec).where(Spec.tenant_id == tenant.id))
    return list(rows.scalars().all())
