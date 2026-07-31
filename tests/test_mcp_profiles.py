"""Profile discovery over MCP: what an agent may build against.

list_profiles offers the packaged standards and every published specification,
so the follow-up tools -- get_profile_schema, get_profile_relationships,
create_dataset -- have to resolve the same names, or the agent is sent in a
circle: told the profile exists, then told to confirm it exists.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset
from tests.mcp_helpers import _calling_with, _tool, _user_with_token


async def test_profiles_include_published_specs(server, session: AsyncSession) -> None:
    """Publishing shares a specification with every user, so an agent can use
    one it did not write."""
    from metaseed_hub.models import SpecStatus
    from tests.factories import make_spec

    tenant_a, user_a, secret_a, _a = await _user_with_token(
        session, slug="mcp00010", email="reader@example.org"
    )
    tenant_b, user_b, _sb, _b = await _user_with_token(
        session, slug="mcp00011", email="author@example.org"
    )
    session.add(
        make_spec(
            tenant=tenant_b,
            created_by=user_b,
            name="SharedSpec",
            status=SpecStatus.PUBLISHED,
        )
    )
    await session.commit()

    list_profiles = await _tool(server, "list_profiles")
    with _calling_with(secret_a):
        result = json.loads(await list_profiles())

    assert "SharedSpec" in [p["name"] for p in result["published"]]
    assert result["built_in"], "the packaged standards are still offered"


def _shared_spec_data() -> dict:
    """spec_data as the publish flow stores it: SpecBuilderState with a spec."""
    from metaseed.specs.builder import SpecBuilder

    from metaseed_hub.ui.spec_builder.state import SpecBuilderState

    builder = SpecBuilder.empty("SharedSpec", "1.0", description="published for everyone")
    builder.add_entity("Sample", description="One sample")
    builder.add_field("Sample", "name", "string", required=True)
    builder.set_root_entity("Sample")
    return SpecBuilderState(spec=builder.spec).to_dict()


class TestPublishedSpecs:
    """Published specifications are usable, not merely advertised.

    list_profiles offers them, so get_profile_schema and create_dataset must
    resolve them too; otherwise the agent is sent in a circle — told the
    profile exists, then told to confirm it exists.
    """

    async def _published(self, session: AsyncSession, *, slug: str) -> str:
        from metaseed_hub.models import SpecStatus
        from tests.factories import make_spec

        tenant, user, secret, _token = await _user_with_token(
            session, slug=slug, email=f"{slug}@example.org"
        )
        session.add(
            make_spec(
                tenant=tenant,
                created_by=user,
                name="SharedSpec",
                version="1.0",
                spec_data=_shared_spec_data(),
                status=SpecStatus.PUBLISHED,
            )
        )
        await session.commit()
        return secret

    async def test_list_profiles_carries_versions_for_everything(
        self, server, session: AsyncSession
    ) -> None:
        """Every follow-up tool requires an exact version, so each entry must
        say which exist."""
        secret = await self._published(session, slug="pub00001")

        list_profiles = await _tool(server, "list_profiles")
        with _calling_with(secret):
            result = json.loads(await list_profiles())

        assert all(p["versions"] for p in result["built_in"])
        miappe = next(p for p in result["built_in"] if p["name"] == "miappe")
        assert "1.1" in miappe["versions"]
        shared = next(p for p in result["published"] if p["name"] == "SharedSpec")
        assert shared["versions"] == ["1.0"]

    async def test_the_schema_of_a_published_spec_is_readable(
        self, server, session: AsyncSession
    ) -> None:
        secret = await self._published(session, slug="pub00002")

        schema = await _tool(server, "get_profile_schema")
        with _calling_with(secret):
            result = json.loads(await schema("SharedSpec", "1.0"))

        assert result["root_entity"] == "Sample"
        fields = result["entities"]["Sample"]["fields"]
        assert [f["name"] for f in fields] == ["name"]
        assert fields[0]["required"] is True

    async def test_a_dataset_on_a_published_spec_can_be_created_and_edited(
        self, server, session: AsyncSession
    ) -> None:
        """The whole advertised flow: create against the spec, then add data."""
        from sqlalchemy import select

        from metaseed_hub.database import db

        secret = await self._published(session, slug="pub00003")

        create_ds = await _tool(server, "create_dataset")
        create_entity = await _tool(server, "create_entity")
        listing = await _tool(server, "list_entities")
        with _calling_with(secret):
            await create_ds("spec-backed", "SharedSpec", "1.0")
            await create_entity("spec-backed", "Sample", {"name": "S1"})
            listed = json.loads(await listing("spec-backed"))

        assert [e["entity_type"] for e in listed["entities"]] == ["Sample"]
        async with db.session_factory() as check:
            row = (
                await check.execute(select(Dataset).where(Dataset.name == "spec-backed"))
            ).scalar_one()
        assert row.spec_id is not None, "the dataset must remember which spec defines it"
        assert row.profile == "sharedspec"


class TestProfileRelationships:
    """The hierarchy map an agent needs before building relationally."""

    async def test_a_built_in_profile_reports_its_hierarchy(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="rel00001", email="rel00001@example.org"
        )

        relationships = await _tool(server, "get_profile_relationships")
        with _calling_with(secret):
            result = json.loads(await relationships("miappe", "1.1"))

        assert result["profile"] == "miappe"
        assert result["version"] == "1.1"
        assert result["root_entity"] == "Investigation"
        investigation = result["hierarchy"]["Investigation"]
        assert "Study" in investigation["children"]
        assert investigation["identifier"] == "unique_id"
        assert isinstance(investigation["cross_references"], dict)

    async def test_a_published_spec_reports_relationships_too(
        self, server, session: AsyncSession
    ) -> None:
        """list_profiles offers published specs, so this must resolve them."""
        from metaseed.specs.builder import SpecBuilder

        from metaseed_hub.models import SpecStatus
        from metaseed_hub.ui.spec_builder.state import SpecBuilderState
        from tests.factories import make_spec

        tenant, user, secret, _token = await _user_with_token(
            session, slug="rel00002", email="rel00002@example.org"
        )
        builder = SpecBuilder.empty("RelSpec", "1.0")
        builder.add_entity("Sample")
        builder.add_entity("Measurement")
        builder.add_field("Sample", "name", "string", required=True)
        builder.add_field("Sample", "measurements", "list", items="Measurement")
        builder.set_root_entity("Sample")
        session.add(
            make_spec(
                tenant=tenant,
                created_by=user,
                name="RelSpec",
                version="1.0",
                spec_data=SpecBuilderState(spec=builder.spec).to_dict(),
                status=SpecStatus.PUBLISHED,
            )
        )
        await session.commit()

        relationships = await _tool(server, "get_profile_relationships")
        with _calling_with(secret):
            result = json.loads(await relationships("RelSpec", "1.0"))

        assert result["root_entity"] == "Sample"
        assert result["hierarchy"]["Sample"]["children"] == ["Measurement"]

    async def test_an_unknown_profile_is_refused(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="rel00003", email="rel00003@example.org"
        )

        relationships = await _tool(server, "get_profile_relationships")
        with _calling_with(secret), pytest.raises(ValueError, match="list_profiles"):
            await relationships("no-such-profile", "1.0")
