"""A published specification is one explorer entry with its versions beside it.

Each published version was its own entry keyed by its id, so a specification
published four times showed as four identical ``imaging (Published)`` entries,
each with a version selector holding one version.
"""

import pytest
from metaseed.specs.schema import ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.ui.explore_routes import _build_explore_catalog, load_profile_spec
from tests.factories import make_spec, make_tenant, make_user


@pytest.mark.asyncio
async def test_published_versions_share_one_entry(session: AsyncSession) -> None:
    tenant = make_tenant(slug="grp00001")
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant)
    session.add(owner)
    await session.flush()
    for version, description in (("1.1", "second"), ("1.0", "first")):
        data = ProfileSpec(name="imaging", version=version, description=description)
        session.add(
            make_spec(
                tenant=tenant,
                created_by=owner,
                name="imaging",
                version=version,
                spec_data=data.model_dump(mode="json"),
            )
        )
    await session.commit()

    caller = TokenUser(sub="viewer", email="v@example.org", name="V", roles=[])
    profiles, versions, names = await _build_explore_catalog(session, caller)

    published = [p for p in profiles if p.startswith("spec:")]
    assert published == ["spec:imaging"]
    assert versions["spec:imaging"] == ["1.0", "1.1"]
    assert names["spec:imaging"] == "imaging (Published)"
    for version, description in (("1.0", "first"), ("1.1", "second")):
        loaded = await load_profile_spec(session, "spec:imaging", version, None)
        assert loaded is not None
        assert loaded[1].description == description
