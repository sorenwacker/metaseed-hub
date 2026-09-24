"""The drafts list groups the versions of one specification.

A draft is one name at one version, so holding three versions of a profile is
ordinary. Listed flat, that is three cards carrying the same title and nothing
but a version number to tell them apart; eight CropXR drafts filled the page
with six repetitions of three names.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.spec_builder.grouping import group_by_specification
from tests.factories import make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio


def _spec_data(name: str, version: str) -> dict:
    return {
        "spec": {
            "name": name,
            "display_name": name.replace("-", " ").title(),
            "version": version,
            "root_entity": "Sample",
            "entities": {"Sample": {"description": "a sample", "fields": []}},
        }
    }


async def _drafts(session: AsyncSession, pairs):
    sub = f"sub-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(user)
    await session.flush()
    made = []
    for name, version in pairs:
        draft = make_spec_draft(
            tenant=tenant,
            user=user,
            name=name,
            version=version,
            spec_data=_spec_data(name, version),
        )
        session.add(draft)
        made.append(draft)
    await session.commit()
    return made


async def test_one_group_per_specification_newest_version_first(
    session: AsyncSession,
) -> None:
    drafts = await _drafts(
        session,
        [
            ("cropxr-sequencing", "1.2"),
            ("cropxr-phenotyping", "1.1"),
            ("cropxr-sequencing", "1.10"),
            ("cropxr-sequencing", "1.3"),
        ],
    )

    groups = group_by_specification(drafts)

    assert [g.name for g in groups] == ["cropxr-phenotyping", "cropxr-sequencing"]
    sequencing = groups[1]
    # 1.10 is a later minor than 1.3, which a string sort gets wrong.
    assert [d.version for d in sequencing.drafts] == ["1.10", "1.3", "1.2"]
    assert sequencing.label == "Cropxr Sequencing"


async def test_a_version_that_is_not_two_numbers_still_lists(
    session: AsyncSession,
) -> None:
    """A draft is not refused for its version string, so the list must not be
    either. Unparseable versions sort last rather than raising."""
    drafts = await _drafts(session, [("odd", "draft-two"), ("odd", "1.0")])

    groups = group_by_specification(drafts)

    assert [d.version for d in groups[0].drafts] == ["1.0", "draft-two"]
