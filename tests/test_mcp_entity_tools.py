"""The entity-level MCP tools: single edits, batches, and the stored format.

Editing one entity is what keeps an agent from resending a whole dataset for
every change, so these assert the entity lands in the stored dataset rather
than merely that the call returned -- and that what lands is the tree envelope
the web UI reads.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset
from tests.factories import make_dataset
from tests.mcp_helpers import _calling_with, _tool, _tree_nodes, _user_with_token


class TestEntityTools:
    """Editing one entity without rewriting the dataset.

    With only save_dataset an agent had to resend everything to change a field,
    which overwrites whatever else changed meanwhile. These assert the entity
    actually lands in the stored dataset, not merely that the call returned.
    """

    async def _dataset(self, session: AsyncSession, *, slug: str, name: str):
        tenant, _user, secret, _token = await _user_with_token(
            session, slug=slug, email=f"{slug}@example.org"
        )
        session.add(make_dataset(tenant=tenant, name=name, profile="miappe", version="1.1"))
        await session.commit()
        return tenant, secret

    async def _stored(self, name: str) -> dict:
        from sqlalchemy import select

        from metaseed_hub.database import db

        async with db.session_factory() as check:
            row = (await check.execute(select(Dataset).where(Dataset.name == name))).scalar_one()
            return row.data or {}

    async def test_creating_an_entity_persists_it(self, server, session: AsyncSession) -> None:
        _t, secret = await self._dataset(session, slug="ent00001", name="ds1")

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            result = json.loads(await create("ds1", "Investigation", {"title": "My study"}))

        assert result["entity_type"] == "Investigation"
        nodes = _tree_nodes(await self._stored("ds1"))
        assert [n["entity_type"] for n in nodes] == ["Investigation"]
        assert nodes[0]["data"]["title"] == "My study"

    async def test_creating_an_entity_reports_what_is_still_missing(
        self, server, session: AsyncSession
    ) -> None:
        """An incomplete entity is accepted, but the agent is told."""
        _t, secret = await self._dataset(session, slug="ent00002", name="ds2")

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            result = json.loads(await create("ds2", "Investigation", {}))

        assert result["valid"] is False
        assert any(i["rule"] == "required_fields" for i in result["issues"])

    async def test_updating_leaves_other_fields_alone(self, server, session: AsyncSession) -> None:
        """The whole point of entity-level editing."""
        _t, secret = await self._dataset(session, slug="ent00003", name="ds3")

        create = await _tool(server, "create_entity")
        update = await _tool(server, "update_entity")
        with _calling_with(secret):
            created = json.loads(
                await create("ds3", "Investigation", {"title": "Keep", "unique_id": "U1"})
            )
            await update("ds3", created["id"], {"unique_id": "U2"})

        entity = _tree_nodes(await self._stored("ds3"))[0]["data"]
        assert entity["unique_id"] == "U2", "the named field changed"
        assert entity["title"] == "Keep", "the unnamed field survived"

    async def test_deleting_removes_only_that_entity(self, server, session: AsyncSession) -> None:
        _t, secret = await self._dataset(session, slug="ent00004", name="ds4")

        create = await _tool(server, "create_entity")
        delete = await _tool(server, "delete_entity")
        with _calling_with(secret):
            a = json.loads(await create("ds4", "Investigation", {"title": "A"}))
            await create("ds4", "Investigation", {"title": "B"})
            await delete("ds4", a["id"])

        titles = [n["data"]["title"] for n in _tree_nodes(await self._stored("ds4"))]
        assert titles == ["B"]

    async def test_an_edit_snapshots_the_previous_state(
        self, server, session: AsyncSession
    ) -> None:
        """Entity edits are many small writes; each must stay recoverable."""
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import DatasetVersion

        _t, secret = await self._dataset(session, slug="ent00005", name="ds5")

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            await create("ds5", "Investigation", {"title": "One"})
            await create("ds5", "Investigation", {"title": "Two"})

        async with db.session_factory() as check:
            versions = (await check.execute(select(DatasetVersion))).scalars().all()
        assert len(versions) == 2, "each edit leaves a way back"

    async def test_entities_can_be_listed_and_read(self, server, session: AsyncSession) -> None:
        _t, secret = await self._dataset(session, slug="ent00006", name="ds6")

        create = await _tool(server, "create_entity")
        listing = await _tool(server, "list_entities")
        get = await _tool(server, "get_entity")
        with _calling_with(secret):
            made = json.loads(await create("ds6", "Investigation", {"title": "Findable"}))
            listed = json.loads(await listing("ds6"))
            one = json.loads(await get("ds6", made["id"]))

        assert [e["entity_type"] for e in listed["entities"]] == ["Investigation"]
        assert one["data"]["title"] == "Findable"

    async def test_another_users_dataset_cannot_be_edited(
        self, server, session: AsyncSession
    ) -> None:
        _ta, secret_a = await self._dataset(session, slug="ent00007", name="mine")
        tenant_b, _ub, _sb, _b = await _user_with_token(
            session, slug="ent00008", email="other@example.org"
        )
        session.add(make_dataset(tenant=tenant_b, name="theirs", profile="miappe"))
        await session.commit()

        create = await _tool(server, "create_entity")
        with _calling_with(secret_a), pytest.raises(ValueError, match="No dataset named"):
            await create("theirs", "Investigation", {"title": "nope"})


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


class TestTreeFormat:
    """MCP reads and writes the tree format the hub actually stores.

    The web UI persists ``client.serialize(format="tree")``; the MCP tools once
    read only the legacy flat envelope, so every dataset a person built in the
    browser looked empty over MCP, and any MCP edit replaced the tree with a
    flat payload the UI's readers cannot see.
    """

    @staticmethod
    def _web_built_dataset() -> dict:
        """A dataset exactly as the web UI's EntityService stores it."""
        from metaseed import MetaseedClient

        client = MetaseedClient(profile="miappe", version="1.1")
        inv = client.create_entity("Investigation", {"title": "Web-built"}, skip_validation=True)
        client.create_entity(
            "Study", {"title": "First study"}, parent_id=inv.id, skip_validation=True
        )
        return client.serialize(format="tree")

    async def _seeded(self, session: AsyncSession, *, slug: str, name: str, data: dict) -> str:
        tenant, _user, secret, _token = await _user_with_token(
            session, slug=slug, email=f"{slug}@example.org"
        )
        session.add(
            make_dataset(tenant=tenant, name=name, profile="miappe", version="1.1", data=data)
        )
        await session.commit()
        return secret

    async def test_a_web_built_dataset_is_visible_over_mcp(
        self, server, session: AsyncSession
    ) -> None:
        """The dataset an agent is pointed at is usually web-built."""
        secret = await self._seeded(
            session, slug="tree0001", name="webds", data=self._web_built_dataset()
        )

        listing = await _tool(server, "list_entities")
        with _calling_with(secret):
            listed = json.loads(await listing("webds"))

        types = sorted(e["entity_type"] for e in listed["entities"])
        assert types == ["Investigation", "Study"], "tree-format entities must be listed"

    async def test_a_tree_entity_can_be_read_by_id(self, server, session: AsyncSession) -> None:
        secret = await self._seeded(
            session, slug="tree0002", name="webread", data=self._web_built_dataset()
        )

        listing = await _tool(server, "list_entities")
        get = await _tool(server, "get_entity")
        with _calling_with(secret):
            listed = json.loads(await listing("webread", entity_type="Study"))
            one = json.loads(await get("webread", listed["entities"][0]["id"]))

        assert one["entity_type"] == "Study"
        assert one["data"]["title"] == "First study"

    async def test_a_legacy_flat_dataset_still_reads(self, server, session: AsyncSession) -> None:
        """Rows written before the tree format load through the same facade."""
        from metaseed import MetaseedClient

        client = MetaseedClient(profile="miappe", version="1.1")
        client.create_entity("Investigation", {"title": "Old flat"}, skip_validation=True)
        secret = await self._seeded(
            session, slug="tree0003", name="flatds", data=client.serialize()
        )

        listing = await _tool(server, "list_entities")
        with _calling_with(secret):
            listed = json.loads(await listing("flatds"))

        assert [e["entity_type"] for e in listed["entities"]] == ["Investigation"]
        assert listed["entities"][0]["data"]["title"] == "Old flat"

    async def test_an_mcp_edit_stores_the_tree_the_web_ui_reads(
        self, server, session: AsyncSession
    ) -> None:
        """Round trip into the UI's own reader, not just back through MCP."""
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade

        secret = await self._seeded(session, slug="tree0004", name="edited", data={})

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            await create("edited", "Investigation", {"title": "From MCP"})

        async with db.session_factory() as check:
            row = (
                await check.execute(select(Dataset).where(Dataset.name == "edited"))
            ).scalar_one()
            assert "tree" in row.data, "the hub's canonical storage is the tree envelope"
            assert "entities" not in row.data, "no flat payload may replace the tree"
            state = await ensure_dataset_facade(row, check)
        assert [n.entity_type for n in state.entity_tree] == ["Investigation"]

    async def test_an_mcp_edit_keeps_a_web_built_dataset_intact(
        self, server, session: AsyncSession
    ) -> None:
        """Adding one entity must not overwrite what the browser built."""
        from sqlalchemy import select

        from metaseed_hub.database import db

        secret = await self._seeded(
            session, slug="tree0005", name="augmented", data=self._web_built_dataset()
        )

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            await create("augmented", "Investigation", {"title": "Second"})

        async with db.session_factory() as check:
            row = (
                await check.execute(select(Dataset).where(Dataset.name == "augmented"))
            ).scalar_one()
        titles = sorted(n["data"].get("title", "") for n in _tree_nodes(row.data))
        assert titles == ["First study", "Second", "Web-built"]
