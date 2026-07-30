"""The correctability, batch, relationship, and ontology MCP tool families.

These extend tests/test_mcp_endpoint.py, whose helpers and fixtures are reused
by import; the isolation and persistence conventions asserted there hold for
every tool added here. Ontology lookups are mocked at the service layer the hub
already uses (metaseed's OntologyService), so no test reaches EMBL-EBI.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
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


async def test_the_instructions_mention_correctability(server) -> None:
    """An agent that does not know a draft is correctable rebuilds it."""
    instructions = server.instructions or ""
    for expected in ("spec_update_field", "spec_rename_entity", "spec_status"):
        assert expected in instructions, f"{expected} is not mentioned"
