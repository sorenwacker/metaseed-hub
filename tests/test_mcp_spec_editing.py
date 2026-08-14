"""Correcting a specification draft in place: entities, fields and rules.

An agent that cannot fix a draft rebuilds it, losing everything the draft
already holds. The mutations are metaseed's ``SpecBuilder``'s, already shared
with the web UI, so these assert the draft in the database actually changed --
including the field attributes that make a spec a tree rather than a flat list.
"""

from __future__ import annotations

import json

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from tests.mcp_helpers import _calling_with, _drafting, _field, _spec_of, _tool


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


class TestSpecFieldMarkers:
    """A field must be able to say what it *is*, not only what values it holds.

    The markers metaseed's ``FieldSpec`` carries -- ``is_identifier`` and
    ``is_label`` above all -- decide which field identifies an entity and which
    one labels it. They were unreachable over MCP, so a spec built through the
    hub could not declare them and fell back on the positional convention.
    """

    async def test_every_marker_survives_the_round_trip(
        self, server, session: AsyncSession
    ) -> None:
        """Set on add, read back from the previewed YAML, so the assertion is
        about the serialized spec rather than the tool's own echo."""
        secret = await _drafting(server, session, slug="mrk00001", name="Marked")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        preview = await _tool(server, "spec_preview_yaml")
        with _calling_with(secret):
            await add_entity("Marked", "Sample")
            await add_field(
                "Marked",
                "Sample",
                "sample_id",
                "string",
                codename="samp",
                ontologies=["obi"],
                unique_within="Study",
                dcat="dct:identifier",
                owns=True,
                is_identifier=True,
                is_label=True,
                example="S-001",
                options=["a", "b"],
                unit="mm",
                label="Sample identifier",
                tier="required",
            )
            previewed = json.loads(await preview("Marked"))["yaml"]

        entities = yaml.safe_load(previewed)["entities"]
        field = next(f for f in entities["Sample"]["fields"] if f["name"] == "sample_id")
        assert field["codename"] == "samp"
        assert field["ontologies"] == ["obi"]
        assert field["unique_within"] == "Study"
        assert field["dcat"] == "dct:identifier"
        assert field["owns"] is True
        assert field["is_identifier"] is True
        assert field["is_label"] is True
        assert field["example"] == "S-001"
        assert field["options"] == ["a", "b"]
        assert field["unit"] == "mm"
        assert field["label"] == "Sample identifier"
        assert field["tier"] == "required"

    async def test_updating_one_marker_leaves_the_others_alone(
        self, server, session: AsyncSession
    ) -> None:
        """An omitted marker means "unchanged", as an omitted constraint does."""
        secret = await _drafting(server, session, slug="mrk00002", name="MarkedPartly")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        update_field = await _tool(server, "spec_update_field")
        with _calling_with(secret):
            await add_entity("MarkedPartly", "Sample")
            await add_field(
                "MarkedPartly", "Sample", "name", "string", unit="mm", tier="recommended"
            )
            await update_field("MarkedPartly", "Sample", "name", is_label=True)

        field = _field(await _spec_of("MarkedPartly"), "Sample", "name")
        assert field["is_label"] is True, "the named marker was set"
        assert field["unit"] == "mm", "the unnamed marker survived"
        assert field["tier"] == "recommended", "the unnamed marker survived"

    async def test_an_unset_marker_is_absent_rather_than_false(
        self, server, session: AsyncSession
    ) -> None:
        """``owns: false`` in the spec would record that a marker was once
        toggled, giving an otherwise identical spec a second content hash."""
        secret = await _drafting(server, session, slug="mrk00003", name="MarkedEmpty")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        update_field = await _tool(server, "spec_update_field")
        preview = await _tool(server, "spec_preview_yaml")
        with _calling_with(secret):
            await add_entity("MarkedEmpty", "Sample")
            # owns=False on add, and a marker set then emptied on update: both
            # are "no marker", not "a marker whose value is false or empty".
            await add_field("MarkedEmpty", "Sample", "name", "string", owns=False, unit="mm")
            await update_field("MarkedEmpty", "Sample", "name", unit="")
            previewed = json.loads(await preview("MarkedEmpty"))["yaml"]

        assert "owns" not in previewed
        assert "unit" not in previewed
        field = _field(await _spec_of("MarkedEmpty"), "Sample", "name")
        assert field.get("owns") is None
        assert field.get("unit") is None

    async def test_a_declared_identifier_is_what_the_built_spec_indexes_by(
        self, server, session: AsyncSession
    ) -> None:
        """The point of the marker: it must beat the positional convention that
        would otherwise pick the first field."""
        from metaseed.facade.core import ProfileFacade

        from metaseed_hub.ui.spec_builder.state import SpecBuilderState

        secret = await _drafting(server, session, slug="mrk00004", name="Identified")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Identified", "Sample")
            await add_field("Identified", "Sample", "title", "string")
            await add_field("Identified", "Sample", "accession", "string", is_identifier=True)

        stored = await _spec_of("Identified")
        spec = SpecBuilderState.from_dict({"spec": stored}).spec
        assert spec is not None
        facade = ProfileFacade(spec.name, spec.version, spec=spec)
        assert facade.require_helper("Sample").identifier_field == "accession"

    async def test_an_invalid_marker_value_is_refused_and_changes_nothing(
        self, server, session: AsyncSession
    ) -> None:
        """The allowed values live in metaseed's schema, so the hub reports the
        refusal instead of failing on the assignment mid-edit."""
        secret = await _drafting(server, session, slug="mrk00005", name="MarkedBadly")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        update_field = await _tool(server, "spec_update_field")
        with _calling_with(secret):
            await add_entity("MarkedBadly", "Sample")
            await add_field("MarkedBadly", "Sample", "name", "string", description="kept")
            with pytest.raises(ValueError, match="tier"):
                await update_field(
                    "MarkedBadly", "Sample", "name", description="changed", tier="whenever"
                )

        field = _field(await _spec_of("MarkedBadly"), "Sample", "name")
        assert field["description"] == "kept", "the refused call still changed the field"
        assert field.get("tier") is None

    async def test_an_invalid_marker_value_is_refused_on_add(
        self, server, session: AsyncSession
    ) -> None:
        """The same check on the add path, which mutates too."""
        secret = await _drafting(server, session, slug="mrk00006", name="MarkedBadlyToo")
        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("MarkedBadlyToo", "Sample")
            with pytest.raises(ValueError, match="tier"):
                await add_field("MarkedBadlyToo", "Sample", "name", "string", tier="whenever")

        assert (await _spec_of("MarkedBadlyToo"))["entities"]["Sample"]["fields"] == []


def test_the_field_tools_offer_every_marker_metaseed_defines() -> None:
    """Hardcoding the names here would let the hub and metaseed drift apart."""
    import inspect

    from metaseed.specs.builder import FIELD_MARKER_NAMES

    from metaseed_hub.mcp import create_mcp_server

    server = create_mcp_server()
    for tool in ("spec_add_field", "spec_update_field"):
        signature = inspect.signature(server._tool_manager.get_tool(tool).fn)
        for name in FIELD_MARKER_NAMES:
            assert name in signature.parameters, f"{tool} cannot set {name}"


class TestMultiEntityRulesAreAuthorable:
    """applies_to accepts a list, as the spec schema always has.

    The tool narrowed it to str, so a rule scoped to several entities —
    `applies_to: list[str] | str` in metaseed's schema — was unauthorable
    over MCP: the caller had to write one copy of the rule per entity.
    """

    @pytest.mark.parametrize("tool_name", ["spec_add_rule", "spec_update_rule"])
    async def test_the_declared_schema_admits_a_list(self, server, tool_name) -> None:
        """The direct-call harness bypasses transport validation; a real MCP
        client obeys the declared schema, so the annotation is the contract."""
        import inspect

        fn = await _tool(server, tool_name)
        annotation = inspect.signature(fn).parameters["applies_to"].annotation
        assert "list" in str(annotation), annotation

    async def test_a_rule_can_apply_to_two_entities(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="fix00030", name="Multi")
        add_entity = await _tool(server, "spec_add_entity")
        add_rule = await _tool(server, "spec_add_rule")
        with _calling_with(secret):
            await add_entity("Multi", "Study")
            await add_entity("Multi", "Sample")
            await add_rule(
                "Multi",
                "shared_pattern",
                type="pattern",
                field="name",
                pattern="^[a-z]+$",
                applies_to=["Study", "Sample"],
                message="lowercase names",
            )
            spec = await _spec_of("Multi")
            assert spec["validation_rules"][0]["applies_to"] == ["Study", "Sample"]
