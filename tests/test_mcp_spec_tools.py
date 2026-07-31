"""The correctability, batch, relationship, and ontology MCP tool families.

These extend tests/test_mcp_endpoint.py, whose helpers and fixtures are reused
by import; the isolation and persistence conventions asserted there hold for
every tool added here. Ontology lookups are mocked at the service layer the hub
already uses (metaseed's OntologyService), so no test reaches EMBL-EBI.
"""

from __future__ import annotations

import inspect
import json
import re
from unittest.mock import patch

import pytest
import yaml
from metaseed.services.ontology import OntologySearchResult, OntologyTerm
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.mcp import NotAuthenticatedError
from tests.factories import make_dataset
from tests.test_mcp_endpoint import (
    _calling_with,
    _tool,
    _tree_nodes,
    _user_with_token,
)

# Imported under an alias and re-exposed by assignment: pytest discovers the
# fixture by module attribute name, and a plain `import server` would trip
# F811 on every test whose `server` parameter shadows it.
from tests.test_mcp_endpoint import server as mcp_server

server = mcp_server


async def _drafting(server, session: AsyncSession, *, slug: str, name: str) -> str:
    """A fresh draft owned by a fresh user; returns the user's token secret."""
    _t, _u, secret, _token = await _user_with_token(session, slug=slug, email=f"{slug}@example.org")
    create = await _tool(server, "spec_create")
    with _calling_with(secret):
        await create(name, "1.0", "a test specification")
    return secret


async def _spec_of(name: str) -> dict:
    """The draft's stored spec dict, read back through the app's own factory."""
    from sqlalchemy import select

    from metaseed_hub.database import db
    from metaseed_hub.models import SpecDraft

    async with db.session_factory() as check:
        draft = (await check.execute(select(SpecDraft).where(SpecDraft.name == name))).scalar_one()
        return draft.spec_data["spec"]


def _field(spec: dict, entity: str, name: str) -> dict:
    return next(f for f in spec["entities"][entity]["fields"] if f["name"] == name)


async def _draft_id(name: str) -> str:
    """The row id of a draft, for assertions about state outside the spec."""
    from sqlalchemy import select

    from metaseed_hub.database import db
    from metaseed_hub.models import SpecDraft

    async with db.session_factory() as check:
        draft = (await check.execute(select(SpecDraft).where(SpecDraft.name == name))).scalar_one()
        return draft.id


class TestSpecCorrectability:
    """Fixing a draft in place instead of rebuilding it.

    The mutations are metaseed's SpecBuilder's, already shared with the web UI;
    these assert the draft in the database actually changed, which is the only
    part the hub adds.
    """

    async def test_updating_an_entity_fixes_a_typo(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="fix00001", name="Typo")
        add_entity = await _tool(server, "spec_add_entity")
        update = await _tool(server, "spec_update_entity")
        with _calling_with(secret):
            await add_entity("Typo", "Study", "A studdy of things")
            await update("Typo", "Study", description="A study of things")

        spec = await _spec_of("Typo")
        assert spec["entities"]["Study"]["description"] == "A study of things"

    async def test_updating_an_entity_leaves_unset_arguments_alone(
        self, server, session: AsyncSession
    ) -> None:
        """None means "unchanged", not "cleared"."""
        secret = await _drafting(server, session, slug="fix00002", name="Partial")
        add_entity = await _tool(server, "spec_add_entity")
        update = await _tool(server, "spec_update_entity")
        with _calling_with(secret):
            await add_entity("Partial", "Sample", "kept", "OBI:0000747")
            await update("Partial", "Sample", description="changed")

        entity = (await _spec_of("Partial"))["entities"]["Sample"]
        assert entity["description"] == "changed"
        assert entity["ontology_term"] == "OBI:0000747"

    async def test_renaming_an_entity_cascades_references(
        self, server, session: AsyncSession
    ) -> None:
        """A rename must chase the name into items links and the root, or the
        spec is left pointing at an entity that no longer exists."""
        secret = await _drafting(server, session, slug="fix00003", name="Renamed")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        set_root = await _tool(server, "spec_set_root_entity")
        rename = await _tool(server, "spec_rename_entity")
        with _calling_with(secret):
            await add_entity("Renamed", "Parent")
            await add_entity("Renamed", "Child")
            await add_field("Renamed", "Parent", "children", "list")
            # Link Parent -> Child the way the instructions teach.
            update_field = await _tool(server, "spec_update_field")
            await update_field("Renamed", "Parent", "children", items="Child")
            await set_root("Renamed", "Parent")
            await rename("Renamed", "Child", "Kid")
            await rename("Renamed", "Parent", "Origin")

        spec = await _spec_of("Renamed")
        assert sorted(spec["entities"]) == ["Kid", "Origin"]
        assert _field(spec, "Origin", "children")["items"] == "Kid"
        assert spec["root_entity"] == "Origin"

    async def test_deleting_the_root_entity_clears_the_root(
        self, server, session: AsyncSession
    ) -> None:
        secret = await _drafting(server, session, slug="fix00004", name="Rootless")
        add_entity = await _tool(server, "spec_add_entity")
        set_root = await _tool(server, "spec_set_root_entity")
        delete = await _tool(server, "spec_delete_entity")
        with _calling_with(secret):
            await add_entity("Rootless", "Study")
            await set_root("Rootless", "Study")
            await delete("Rootless", "Study")

        spec = await _spec_of("Rootless")
        assert "Study" not in spec["entities"]
        assert not spec["root_entity"], "a deleted entity must not stay the root"

    async def test_updating_a_field_changes_only_named_attributes(
        self, server, session: AsyncSession
    ) -> None:
        secret = await _drafting(server, session, slug="fix00005", name="Fielded")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        update_field = await _tool(server, "spec_update_field")
        with _calling_with(secret):
            await add_entity("Fielded", "Study")
            await add_field("Fielded", "Study", "title", "string", description="kept")
            await update_field("Fielded", "Study", "title", required=True)

        field = _field(await _spec_of("Fielded"), "Study", "title")
        assert field["required"] is True, "the named attribute changed"
        assert field["description"] == "kept", "the unnamed attribute survived"
        assert field["type"] == "string"

    async def test_deleting_a_field_removes_it(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="fix00006", name="Trimmed")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        delete_field = await _tool(server, "spec_delete_field")
        with _calling_with(secret):
            await add_entity("Trimmed", "Study")
            await add_field("Trimmed", "Study", "keep", "string")
            await add_field("Trimmed", "Study", "drop", "string")
            await delete_field("Trimmed", "Study", "drop")

        fields = (await _spec_of("Trimmed"))["entities"]["Study"]["fields"]
        assert [f["name"] for f in fields] == ["keep"]

    async def test_moving_a_field_reorders_it(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="fix00007", name="Ordered")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        move = await _tool(server, "spec_move_field")
        with _calling_with(secret):
            await add_entity("Ordered", "Study")
            await add_field("Ordered", "Study", "first", "string")
            await add_field("Ordered", "Study", "second", "string")
            await move("Ordered", "Study", "second", "up")

        fields = (await _spec_of("Ordered"))["entities"]["Study"]["fields"]
        assert [f["name"] for f in fields] == ["second", "first"]

    async def test_rules_can_be_added_updated_and_deleted(
        self, server, session: AsyncSession
    ) -> None:
        secret = await _drafting(server, session, slug="fix00008", name="Ruled")
        add_entity = await _tool(server, "spec_add_entity")
        add_rule = await _tool(server, "spec_add_rule")
        update_rule = await _tool(server, "spec_update_rule")
        delete_rule = await _tool(server, "spec_delete_rule")
        with _calling_with(secret):
            await add_entity("Ruled", "Study")
            await add_rule("Ruled", "needs_study", type="min_count", message="one study at least")
            spec = await _spec_of("Ruled")
            assert [(r["name"], r["message"]) for r in spec["validation_rules"]] == [
                ("needs_study", "one study at least")
            ]

            await update_rule("Ruled", "needs_study", message="two studies at least")
            spec = await _spec_of("Ruled")
            assert spec["validation_rules"][0]["message"] == "two studies at least"
            assert spec["validation_rules"][0]["type"] == "min_count", "unnamed attribute survived"

            await delete_rule("Ruled", "needs_study")
            assert (await _spec_of("Ruled"))["validation_rules"] == []

    async def test_set_metadata_updates_only_what_is_named(
        self, server, session: AsyncSession
    ) -> None:
        secret = await _drafting(server, session, slug="fix00009", name="Meta")
        set_metadata = await _tool(server, "spec_set_metadata")
        with _calling_with(secret):
            await set_metadata("Meta", display_name="Nicely Named")

        spec = await _spec_of("Meta")
        assert spec["display_name"] == "Nicely Named"
        assert spec["version"] == "1.0", "the unnamed metadata survived"
        assert spec["description"] == "a test specification"

    async def test_status_summarizes_the_draft(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="fix00010", name="Summed")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        set_root = await _tool(server, "spec_set_root_entity")
        status = await _tool(server, "spec_status")
        with _calling_with(secret):
            await add_entity("Summed", "Study")
            await add_field("Summed", "Study", "title", "string")
            await set_root("Summed", "Study")
            result = json.loads(await status("Summed"))

        assert result["name"] == "Summed"
        assert result["version"] == "1.0"
        assert result["root_entity"] == "Study"
        assert result["entities"] == {"Study": ["title"]}
        assert result["validation_rules"] == []

    async def test_a_bad_correction_is_refused_by_metaseed(
        self, server, session: AsyncSession
    ) -> None:
        """Rejections come from SpecBuilder and reach the agent as errors."""
        secret = await _drafting(server, session, slug="fix00011", name="Strict2")
        update = await _tool(server, "spec_update_entity")
        with _calling_with(secret), pytest.raises(ValueError, match="not found"):
            await update("Strict2", "NoSuchEntity", description="nope")

    async def test_another_users_draft_is_not_correctable(
        self, server, session: AsyncSession
    ) -> None:
        secret_a = await _drafting(server, session, slug="fix00012", name="MineToo")
        await _drafting(server, session, slug="fix00013", name="TheirsToo")

        rename = await _tool(server, "spec_rename_entity")
        with _calling_with(secret_a), pytest.raises(ValueError, match="No specification draft"):
            await rename("TheirsToo", "A", "B")


_ROUND_TRIP_YAML = """
name: RoundTrip
version: "1.0"
display_name: Round Trip
description: An imported specification
root_entity: Study
entities:
  Study:
    description: A study of things
    fields:
      - name: title
        type: string
        required: true
      - name: samples
        type: list
        items: Sample
  Sample:
    description: A sample
    fields:
      - name: label
        type: string
"""


class TestSpecImportYaml:
    """A YAML document becomes a private draft, the way the web Import page
    already does it — same parser, same draft row, same name-uniqueness."""

    async def test_an_imported_draft_round_trips_through_the_preview(
        self, server, session: AsyncSession
    ) -> None:
        """Import then preview must describe the same specification: nothing
        gained, nothing lost, or the hub's copy is not the user's spec."""
        from metaseed_hub.ui.spec_builder_helpers import parse_spec_from_yaml

        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00001", email="imp00001@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        preview = await _tool(server, "spec_preview_yaml")
        with _calling_with(secret):
            created = json.loads(await import_yaml(_ROUND_TRIP_YAML))
            assert created["name"] == "RoundTrip", "an empty name falls back to the spec's own"
            assert created["version"] == "1.0"
            previewed = json.loads(await preview("RoundTrip"))

        assert parse_spec_from_yaml(previewed["yaml"]) == parse_spec_from_yaml(_ROUND_TRIP_YAML)

    async def test_the_draft_name_argument_wins_over_the_specs_own(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00002", email="imp00002@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret):
            created = json.loads(await import_yaml(_ROUND_TRIP_YAML, "Renamed Import"))

        assert created["name"] == "Renamed Import"
        spec = await _spec_of("Renamed Import")
        assert spec["name"] == "RoundTrip", "the draft name does not rewrite the spec"

    async def test_a_name_collision_is_a_clean_error(self, server, session: AsyncSession) -> None:
        """Same rule as spec_create: draft names are unique per user."""
        secret = await _drafting(server, session, slug="imp00003", name="RoundTrip")
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret), pytest.raises(ValueError, match="already exists"):
            await import_yaml(_ROUND_TRIP_YAML)

    async def test_invalid_yaml_is_a_clean_error(self, server, session: AsyncSession) -> None:
        """A syntax error reads as a ValueError the agent can act on, not a
        stack trace."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00004", email="imp00004@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret), pytest.raises(ValueError, match="Invalid YAML"):
            await import_yaml("entities: [unclosed")

    async def test_a_non_mapping_document_is_refused(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00005", email="imp00005@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret), pytest.raises(ValueError, match="mapping"):
            await import_yaml("- just\n- a list")

    async def test_an_imported_draft_is_scoped_to_its_importer(
        self, server, session: AsyncSession
    ) -> None:
        """Another user of the same tenant can neither see nor edit it."""
        from metaseed_hub.tokens import issue_token
        from tests.factories import make_tenant, make_user

        tenant = make_tenant(slug="imp00006")
        session.add(tenant)
        await session.flush()
        user_a = make_user(tenant=tenant, email="imp00006-a@example.org")
        user_b = make_user(tenant=tenant, email="imp00006-b@example.org")
        session.add_all([user_a, user_b])
        await session.commit()
        secret_a, _ = await issue_token(session, user_a, name="agent")
        secret_b, _ = await issue_token(session, user_b, name="agent")

        import_yaml = await _tool(server, "spec_import_yaml")
        listing = await _tool(server, "list_spec_drafts")
        status = await _tool(server, "spec_status")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret_a):
            await import_yaml(_ROUND_TRIP_YAML, "OnlyMine")
        with _calling_with(secret_b):
            assert json.loads(await listing()) == [], "not visible in the other user's list"
            with pytest.raises(ValueError, match="No specification draft"):
                await status("OnlyMine")
            with pytest.raises(ValueError, match="No specification draft"):
                await add_entity("OnlyMine", "Intruder")


class TestSpecClone:
    """A built-in profile or published specification becomes a private draft."""

    async def test_a_built_in_profile_can_be_cloned(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00001", email="clo00001@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret):
            created = json.loads(await clone("miappe", "1.1", "MyMiappe"))

        assert created["name"] == "MyMiappe"
        spec = await _spec_of("MyMiappe")
        assert spec["root_entity"] == "Investigation"
        assert "Study" in spec["entities"]

    async def test_the_draft_name_defaults_to_the_profiles_own(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00002", email="clo00002@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret):
            created = json.loads(await clone("miappe", "1.1"))

        assert created["name"] == "miappe"

    async def test_a_cloned_draft_is_editable_in_place(self, server, session: AsyncSession) -> None:
        """The clone is the caller's own copy, so edits land in the draft."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00003", email="clo00003@example.org"
        )
        clone = await _tool(server, "spec_clone")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret):
            await clone("miappe", "1.1", "Extended")
            await add_entity("Extended", "Drone", "An aerial platform")

        spec = await _spec_of("Extended")
        assert spec["entities"]["Drone"]["description"] == "An aerial platform"

    async def test_an_unknown_version_lists_the_available_ones(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00004", email="clo00004@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret), pytest.raises(ValueError, match=r"available.*1\.1"):
            await clone("miappe", "99.9")

    async def test_an_unknown_profile_points_at_list_profiles(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00005", email="clo00005@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret), pytest.raises(ValueError, match="list_profiles"):
            await clone("no-such-profile", "1.0")

    async def test_a_name_collision_is_a_clean_error(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="clo00006", name="miappe")
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret), pytest.raises(ValueError, match="already exists"):
            await clone("miappe", "1.1")


class TestBatchCreate:
    """Several entities in one call, with one version snapshot for the batch.

    Per-entity creation would leave one snapshot per entity; a batch is one
    write, so it must leave exactly one way back.
    """

    async def _dataset(self, session: AsyncSession, *, slug: str, name: str) -> str:
        tenant, _user, secret, _token = await _user_with_token(
            session, slug=slug, email=f"{slug}@example.org"
        )
        session.add(make_dataset(tenant=tenant, name=name, profile="miappe", version="1.1"))
        await session.commit()
        return secret

    async def _stored(self, name: str) -> dict:
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import Dataset

        async with db.session_factory() as check:
            row = (await check.execute(select(Dataset).where(Dataset.name == name))).scalar_one()
            return row.data or {}

    async def test_a_hierarchy_is_created_in_one_call(self, server, session: AsyncSession) -> None:
        """parent_index links an item under an earlier item of the same batch,
        which is what makes a root-first hierarchy possible in one call."""
        secret = await self._dataset(session, slug="batch001", name="bd1")

        batch = await _tool(server, "batch_create")
        with _calling_with(secret):
            result = json.loads(
                await batch(
                    "bd1",
                    [
                        {"entity_type": "Investigation", "data": {"title": "Root"}},
                        {"entity_type": "Study", "data": {"title": "S1"}, "parent_index": 0},
                        {"entity_type": "Study", "data": {"title": "S2"}, "parent_index": 0},
                    ],
                )
            )

        assert result["total"] == 3
        assert result["created"] == 3
        assert result["failed"] == 0
        assert all(r["status"] == "created" and r["id"] for r in result["results"])

        data = await self._stored("bd1")
        roots = data["tree"]
        assert [n["entity_type"] for n in roots] == ["Investigation"]
        children = sorted(c["data"]["title"] for c in roots[0]["children"])
        assert children == ["S1", "S2"], "the studies must nest under the batch's own root"

    async def test_the_batch_leaves_exactly_one_snapshot(
        self, server, session: AsyncSession
    ) -> None:
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import DatasetVersion

        secret = await self._dataset(session, slug="batch002", name="bd2")

        batch = await _tool(server, "batch_create")
        with _calling_with(secret):
            await batch(
                "bd2",
                [
                    {"entity_type": "Investigation", "data": {"title": "Root"}},
                    {"entity_type": "Study", "data": {"title": "S1"}, "parent_index": 0},
                ],
            )

        async with db.session_factory() as check:
            versions = (await check.execute(select(DatasetVersion))).scalars().all()
        assert len(versions) == 1, "one batch is one write, so one version"

    async def test_a_failed_item_does_not_sink_the_batch(
        self, server, session: AsyncSession
    ) -> None:
        """Mirrors the standalone server: per-item errors are reported, the
        rest of the batch still lands."""
        secret = await self._dataset(session, slug="batch003", name="bd3")

        batch = await _tool(server, "batch_create")
        with _calling_with(secret):
            result = json.loads(
                await batch(
                    "bd3",
                    [
                        {"entity_type": "Investigation", "data": {"title": "Root"}},
                        {"entity_type": "NoSuchType", "data": {}},
                    ],
                )
            )

        assert result["created"] == 1
        assert result["failed"] == 1
        assert result["results"][1]["status"] == "error"
        assert "NoSuchType" in result["results"][1]["message"]
        nodes = _tree_nodes(await self._stored("bd3"))
        assert [n["entity_type"] for n in nodes] == ["Investigation"]

    async def test_a_parent_index_must_point_at_an_earlier_item(
        self, server, session: AsyncSession
    ) -> None:
        secret = await self._dataset(session, slug="batch004", name="bd4")

        batch = await _tool(server, "batch_create")
        with _calling_with(secret):
            result = json.loads(
                await batch(
                    "bd4",
                    [{"entity_type": "Study", "data": {"title": "S"}, "parent_index": 5}],
                )
            )

        assert result["failed"] == 1
        assert "parent_index" in result["results"][0]["message"]

    async def test_the_batch_reports_what_is_still_missing(
        self, server, session: AsyncSession
    ) -> None:
        """Like every other edit: the agent is told what to fill next."""
        secret = await self._dataset(session, slug="batch005", name="bd5")

        batch = await _tool(server, "batch_create")
        with _calling_with(secret):
            result = json.loads(await batch("bd5", [{"entity_type": "Investigation", "data": {}}]))

        assert result["valid"] is False
        assert any(i["rule"] == "required_fields" for i in result["issues"])

    async def test_another_users_dataset_is_not_batchable(
        self, server, session: AsyncSession
    ) -> None:
        await self._dataset(session, slug="batch006", name="bd6")
        _t, _u, secret_b, _token = await _user_with_token(
            session, slug="batch007", email="batch007@example.org"
        )

        batch = await _tool(server, "batch_create")
        with _calling_with(secret_b), pytest.raises(ValueError, match="No dataset named"):
            await batch("bd6", [{"entity_type": "Investigation", "data": {}}])


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


class _FakeOntologyService:
    """Stands in for metaseed's OntologyService, so no test reaches OLS4."""

    def __init__(
        self,
        results: list[OntologySearchResult] | None = None,
        term: OntologyTerm | None = None,
    ) -> None:
        self._results = results or []
        self._term = term
        self.calls: list[tuple] = []

    async def search(self, query, ontology=None, rows=10, exact=False):
        self.calls.append(("search", query, ontology, rows))
        return list(self._results)

    async def get_term(self, term_id):
        self.calls.append(("get_term", term_id))
        return self._term


def _drought() -> OntologySearchResult:
    return OntologySearchResult(
        term_id="PECO:0007404",
        label="drought environment",
        description="An environment lacking water.",
        ontology="PECO",
        iri="http://purl.obolibrary.org/obo/PECO_0007404",
    )


class TestOntologyTools:
    """Read-only OLS lookups through the service layer the web UI already uses.

    Authenticated but not dataset-scoped: a lookup touches nobody's data, yet
    an open endpoint would let an unauthenticated caller drive traffic through
    the hub (the same reasoning as ui/routes/ontology_api.py).
    """

    async def test_search_returns_results(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00001", email="ont00001@example.org"
        )
        fake = _FakeOntologyService(results=[_drought()])

        search = await _tool(server, "search_ontology")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_ontology_service", return_value=fake),
            _calling_with(secret),
        ):
            result = json.loads(await search("drought", ontology="peco", rows=5))

        assert result["total_found"] == 1
        assert result["results"][0]["id"] == "PECO:0007404"
        assert result["results"][0]["label"] == "drought environment"
        assert result["results"][0]["description"] == "An environment lacking water."
        assert ("search", "drought", "peco", 5) in fake.calls

    async def test_a_lookup_still_requires_a_token(self, server, session: AsyncSession) -> None:
        """Authentication-only is not authentication-optional."""
        fake = _FakeOntologyService(results=[_drought()])

        search = await _tool(server, "search_ontology")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_ontology_service", return_value=fake),
            _calling_with(None),
            pytest.raises(NotAuthenticatedError),
        ):
            await search("drought")

    async def test_a_term_can_be_read_in_detail(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00002", email="ont00002@example.org"
        )
        fake = _FakeOntologyService(
            term=OntologyTerm(
                term_id="PATO:0000015",
                label="color",
                description="A composite chromatic quality.",
                ontology="PATO",
                iri="http://purl.obolibrary.org/obo/PATO_0000015",
                synonyms=["colour"],
            )
        )

        get_term = await _tool(server, "get_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_ontology_service", return_value=fake),
            _calling_with(secret),
        ):
            result = json.loads(await get_term("PATO:0000015"))

        assert result["id"] == "PATO:0000015"
        assert result["label"] == "color"
        assert result["definition"] == "A composite chromatic quality."
        assert result["synonyms"] == ["colour"]

    async def test_a_missing_term_is_an_error_the_agent_can_act_on(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00003", email="ont00003@example.org"
        )
        fake = _FakeOntologyService(term=None)

        get_term = await _tool(server, "get_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_ontology_service", return_value=fake),
            _calling_with(secret),
            pytest.raises(ValueError, match="No ontology term"),
        ):
            await get_term("PATO:9999999")

    async def test_suggestions_are_light(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00004", email="ont00004@example.org"
        )
        fake = _FakeOntologyService(results=[_drought()])

        suggest = await _tool(server, "suggest_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_ontology_service", return_value=fake),
            _calling_with(secret),
        ):
            result = json.loads(await suggest("drou"))

        assert result["suggestions"] == [
            {"id": "PECO:0007404", "label": "drought environment", "ontology": "PECO"}
        ]

    async def test_the_ontology_catalog_can_be_listed(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00005", email="ont00005@example.org"
        )
        catalog = {
            "_embedded": {
                "ontologies": [
                    {
                        "ontologyId": "go",
                        "config": {
                            "title": "Gene Ontology",
                            "preferredPrefix": "GO",
                            "description": "An ontology for gene products.",
                        },
                    }
                ]
            },
            "page": {"totalElements": 1},
        }

        listing = await _tool(server, "list_ontologies")
        with (
            patch(
                "metaseed_hub.mcp._ontology_tools._fetch_ontologies",
                return_value=catalog,
            ),
            _calling_with(secret),
        ):
            result = json.loads(await listing())

        assert result["total"] == 1
        assert result["ontologies"] == [
            {
                "id": "go",
                "name": "Gene Ontology",
                "prefix": "GO",
                "description": "An ontology for gene products.",
            }
        ]

    async def test_an_unreachable_catalog_is_an_error(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00006", email="ont00006@example.org"
        )

        listing = await _tool(server, "list_ontologies")
        with (
            patch("metaseed_hub.mcp._ontology_tools._fetch_ontologies", return_value=None),
            _calling_with(secret),
            pytest.raises(ValueError, match="could not be reached"),
        ):
            await listing()


class TestSpecFieldRelationships:
    """A field can say what it points at, which is what makes a spec a tree.

    Without ``items`` a list field names no child entity, so every draft built
    here is flat and unusable as a profile — the endpoint's own instructions
    teach the linking call, so the tool has to accept it.
    """

    async def test_a_list_field_nests_the_entity_its_items_names(
        self, server, session: AsyncSession
    ) -> None:
        """The end-to-end case: Collection.movies is a list of Movie, and the
        parent identifier and the child's back-reference come with it."""
        secret = await _drafting(server, session, slug="lnk00001", name="Films")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        preview = await _tool(server, "spec_preview_yaml")
        with _calling_with(secret):
            await add_entity("Films", "Collection")
            await add_entity("Films", "Movie")
            await add_field("Films", "Collection", "movies", "list", items="Movie")
            previewed = json.loads(await preview("Films"))["yaml"]

        entities = yaml.safe_load(previewed)["entities"]
        movies = next(f for f in entities["Collection"]["fields"] if f["name"] == "movies")
        assert movies["type"] == "list"
        assert movies["items"] == "Movie", "a list must say what it is a list of"

        identifier = next(f for f in entities["Collection"]["fields"] if f["name"] == "identifier")
        assert identifier["required"] is True, "the parent needs an id to be referenced by"
        back = next(
            f for f in entities["Movie"]["fields"] if f.get("reference") == "Collection.identifier"
        )
        assert back["name"] == "collection_id"

    async def test_a_reference_field_is_persisted(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="lnk00002", name="Referring")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Referring", "Study")
            await add_entity("Referring", "Sample")
            await add_field("Referring", "Study", "unique_id", "string")
            await add_field(
                "Referring", "Sample", "study_id", "string", reference="Study.unique_id"
            )

        assert _field(await _spec_of("Referring"), "Sample", "study_id")["reference"] == (
            "Study.unique_id"
        )

    async def test_a_parent_ref_field_is_persisted(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="lnk00003", name="Parented")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Parented", "Study")
            await add_entity("Parented", "Sample")
            await add_field("Parented", "Sample", "parent_study", "string", parent_ref="Study.name")

        assert _field(await _spec_of("Parented"), "Sample", "parent_study")["parent_ref"] == (
            "Study.name"
        )

    async def test_constraints_round_trip(self, server, session: AsyncSession) -> None:
        """Constraints are the spec's own validation; a field that cannot carry
        them describes less than the profile it is meant to replace."""
        secret = await _drafting(server, session, slug="lnk00004", name="Constrained")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Constrained", "Study")
            await add_field("Constrained", "Study", "status", "string", enum=["draft", "published"])
            await add_field("Constrained", "Study", "replicates", "integer", minimum=1)

        spec = await _spec_of("Constrained")
        assert _field(spec, "Study", "status")["constraints"]["enum"] == ["draft", "published"]
        assert _field(spec, "Study", "replicates")["constraints"]["minimum"] == 1

    async def test_a_field_without_constraints_stores_none(
        self, server, session: AsyncSession
    ) -> None:
        """No constraint argument means no constraints object, not an empty one."""
        secret = await _drafting(server, session, slug="lnk00005", name="Plain")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Plain", "Study")
            await add_field("Plain", "Study", "title", "string")

        assert "constraints" not in _field(await _spec_of("Plain"), "Study", "title")

    async def test_items_can_be_set_on_an_existing_list_field(
        self, server, session: AsyncSession
    ) -> None:
        """The correction path the instructions promise: a list added without
        items is repaired in place rather than deleted and re-added."""
        secret = await _drafting(server, session, slug="lnk00006", name="Repaired")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        update_field = await _tool(server, "spec_update_field")
        with _calling_with(secret):
            await add_entity("Repaired", "Collection")
            await add_entity("Repaired", "Movie")
            await add_field("Repaired", "Collection", "movies", "list")
            await update_field("Repaired", "Collection", "movies", items="Movie")

        assert _field(await _spec_of("Repaired"), "Collection", "movies")["items"] == "Movie"

    async def test_a_dangling_items_target_is_reported(self, server, session: AsyncSession) -> None:
        """A list whose items name no entity is reported by spec_validate, so an
        agent is told rather than left with a link to nowhere."""
        secret = await _drafting(server, session, slug="lnk00007", name="Dangling")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Dangling", "Collection")
            result = json.loads(
                await add_field("Dangling", "Collection", "movies", "list", items="NoSuchEntity")
            )

        assert any("NoSuchEntity" in problem for problem in result["problems"])


class TestSpecDeleteDraft:
    """A draft the caller no longer wants is removable without the web UI.

    Every other draft mutation is available here; without this one an agent can
    only accumulate drafts it cannot clear.
    """

    async def test_a_draft_is_deleted(self, server, session: AsyncSession) -> None:
        from metaseed_hub.ui.spec_builder.cache import state_cache

        secret = await _drafting(server, session, slug="del00001", name="Disposable")
        draft_id = await _draft_id("Disposable")
        delete = await _tool(server, "spec_delete_draft")
        listing = await _tool(server, "list_spec_drafts")
        with _calling_with(secret):
            result = json.loads(await delete("Disposable"))
            assert result == {"deleted": "Disposable"}
            assert json.loads(await listing()) == []

        assert draft_id not in state_cache, "a deleted draft must not stay cached"

    async def test_a_deleted_draft_is_no_longer_editable(
        self, server, session: AsyncSession
    ) -> None:
        secret = await _drafting(server, session, slug="del00002", name="Gone")
        delete = await _tool(server, "spec_delete_draft")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret):
            await delete("Gone")
            with pytest.raises(ValueError, match="No specification draft"):
                await add_entity("Gone", "Study")

    async def test_another_users_draft_is_not_deletable(
        self, server, session: AsyncSession
    ) -> None:
        """Scoped like every other draft tool: a name someone else owns reads as
        absent, not as reachable."""
        from metaseed_hub.tokens import issue_token
        from tests.factories import make_tenant, make_user

        tenant = make_tenant(slug="del00003")
        session.add(tenant)
        await session.flush()
        user_a = make_user(tenant=tenant, email="del00003-a@example.org")
        user_b = make_user(tenant=tenant, email="del00003-b@example.org")
        session.add_all([user_a, user_b])
        await session.commit()
        secret_a, _ = await issue_token(session, user_a, name="agent")
        secret_b, _ = await issue_token(session, user_b, name="agent")

        create = await _tool(server, "spec_create")
        delete = await _tool(server, "spec_delete_draft")
        with _calling_with(secret_a):
            await create("Guarded", "1.0")
        with _calling_with(secret_b), pytest.raises(ValueError, match="No specification draft"):
            await delete("Guarded")

        assert (await _spec_of("Guarded"))["name"] == "Guarded", "the owner still has it"

    async def test_an_unknown_name_is_a_clean_error(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="del00004", name="Kept")
        delete = await _tool(server, "spec_delete_draft")
        with _calling_with(secret), pytest.raises(ValueError, match="No specification draft"):
            await delete("NeverExisted")

    async def test_a_draft_a_dataset_depends_on_is_kept(
        self, server, session: AsyncSession
    ) -> None:
        """Deleting it would leave the dataset without a specification, so the
        agent is told which datasets to deal with first."""
        from sqlalchemy import select

        from metaseed_hub.models import SpecDraft
        from tests.factories import make_dataset

        tenant, _user, secret, _token = await _user_with_token(
            session, slug="del00005", email="del00005@example.org"
        )
        create = await _tool(server, "spec_create")
        with _calling_with(secret):
            await create("InUse", "1.0")

        draft = (
            await session.execute(select(SpecDraft).where(SpecDraft.name == "InUse"))
        ).scalar_one()
        dataset = make_dataset(tenant=tenant, name="dependent", profile="inuse", version="1.0")
        dataset.spec_draft_id = draft.id
        session.add(dataset)
        await session.commit()

        delete = await _tool(server, "spec_delete_draft")
        with _calling_with(secret), pytest.raises(ValueError, match="dependent"):
            await delete("InUse")

        assert (await _spec_of("InUse"))["name"] == "InUse", "the draft survived"


_TOOL_MENTION = re.compile(r"\b(spec_[a-z_]+)\b")
_PARAMETER_MENTION = re.compile(r"\b([a-z_]+)=")


def _sentences(text: str) -> list[str]:
    """Split instructions into sentences, so a tool and its arguments pair up."""
    return [part for part in re.split(r"(?<=\.)\s|\n", text) if part.strip()]


async def test_the_instructions_only_name_tools_and_arguments_that_exist(server) -> None:
    """The instructions are the agent's whole picture of the endpoint.

    They told agents to call spec_add_field with items=<ChildEntityName> while
    the tool had no items parameter, so every agent that followed them hit an
    unexpected-argument error. Checked generally: any spec_* tool named, and any
    ``argument=`` shown, must exist in the registered signatures.
    """
    instructions = server.instructions or ""
    registered = {t.name for t in await server.list_tools()}
    parameters = {
        name: set(inspect.signature(server._tool_manager.get_tool(name).fn).parameters)
        for name in registered
    }
    known_parameters = set().union(*parameters.values())

    for mentioned in sorted(set(_TOOL_MENTION.findall(instructions))):
        assert mentioned in registered, f"the instructions name {mentioned}, which is not a tool"

    for sentence in _sentences(instructions):
        named = sorted(set(_TOOL_MENTION.findall(sentence)) & registered)
        for argument in sorted(set(_PARAMETER_MENTION.findall(sentence))):
            # One tool in the sentence means the argument is that tool's;
            # otherwise it only has to belong to some registered tool.
            expected = parameters[named[0]] if len(named) == 1 else known_parameters
            where = named[0] if len(named) == 1 else "any tool"
            assert argument in expected, (
                f"the instructions show {argument}= but {where} has no such parameter"
            )


async def test_the_instructions_mention_correctability(server) -> None:
    """An agent that does not know a draft is correctable rebuilds it."""
    instructions = server.instructions or ""
    for expected in ("spec_update_field", "spec_rename_entity", "spec_status"):
        assert expected in instructions, f"{expected} is not mentioned"
