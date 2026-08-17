"""A stored entity that does not load must be reported, never silently dropped.

``ensure_dataset_facade`` loads permissively so that one bad node cannot make a
whole dataset unreadable. The cost of that tolerance is that the loaded dataset
is quietly smaller than the stored one -- and since every save serializes the
loaded facade, the next save deletes what did not load. So the tolerance is only
safe if the drops are visible.

They reach people through the two paths that already report dataset problems:
the MCP validation report (agents) and the web validation panel (users). Unlike
spec drift, an unloadable node makes the report say ``valid: false``: the
validator only saw the nodes that loaded, so calling the dataset valid would be
an answer about a subset of it.

Reporting alone does not stop an agent, which reads the tool's return value and
takes a successful edit as success. So the MCP editing context refuses the edit
outright while a node is unloadable, and these tests hold that refusal to its
two halves: it must fire on every mutating tool, and it must not fire on a
dataset that loads cleanly, which is every normal dataset.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset
from metaseed_hub.ui.helpers import get_or_create_csrf_token
from metaseed_hub.ui.helpers.load_report import SKIPPED_NODE_RULE, skipped_node_issues
from tests.factories import make_dataset, make_tenant, make_user
from tests.mcp_helpers import _calling_with, _tool, _user_with_token


def _tree_with_an_unloadable_node() -> dict[str, Any]:
    """A miappe payload whose first node names an entity type no profile defines."""
    return {
        "profile": "miappe",
        "version": "1.1",
        "tree": [
            {
                "id": "gone-1",
                "entity_type": "Bogus",
                "label": "written by an older specification",
                "data": {"identifier": "B-1"},
                "children": [],
            },
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "kept",
                "data": {"identifier": "INV-1"},
                "children": [],
            },
        ],
    }


class TestTheIssueRecords:
    """One skipped node becomes one issue an agent or user can act on."""

    async def test_the_issue_names_the_type_the_reason_and_the_loss(self) -> None:
        from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade

        dataset = Mock(
            id="ds-report",
            profile="miappe",
            version="1.1",
            data={
                "tree": [
                    {
                        "id": "gone-1",
                        "entity_type": "Bogus",
                        "label": "?",
                        "children": [{"id": "c-1", "entity_type": "Study", "children": []}],
                    }
                ]
            },
            spec_draft_id=None,
            spec_id=None,
        )
        skipped: list[Any] = []
        await ensure_dataset_facade(dataset, Mock(), on_skip=skipped.append)

        issues = skipped_node_issues(skipped)

        assert len(issues) == 1
        assert issues[0]["rule"] == SKIPPED_NODE_RULE
        assert issues[0]["entity_id"] == "gone-1"
        message = issues[0]["message"]
        assert "Bogus" in message
        assert "1 node(s) below it were dropped" in message


async def _dataset_with_an_unloadable_node(name: str, secret: str, server) -> None:
    """Create a dataset over MCP and give it a payload with one bad node.

    Args:
        name: The dataset name to create in the caller's account.
        secret: The token secret the creating call acts as.
        server: The MCP server fixture.
    """
    from metaseed_hub.database import db

    create_dataset = await _tool(server, "create_dataset")
    with _calling_with(secret):
        await create_dataset(name, "miappe", "1.1")

    async with db.session_factory() as write:
        found = await write.execute(select(Dataset).where(Dataset.name == name))
        dataset = found.scalar_one()
        dataset.data = _tree_with_an_unloadable_node()
        flag_modified(dataset, "data")
        await write.commit()


class TestTheMcpReport:
    """An agent asking whether a dataset is complete is told what did not load."""

    async def test_the_report_carries_the_skipped_node(self, server, session) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="skip-mcp", email="skip-mcp@example.org"
        )
        await _dataset_with_an_unloadable_node("Lossy", secret, server)
        validate = await _tool(server, "validate_dataset")

        with _calling_with(secret):
            report = json.loads(await validate("Lossy"))

        assert any(issue["rule"] == SKIPPED_NODE_RULE for issue in report["issues"]), report
        assert report["valid"] is False, report
        assert SKIPPED_NODE_RULE in report["next_step"], report

    async def test_a_dataset_that_loads_cleanly_reports_no_skips(self, server, session) -> None:
        """The report must stay quiet when nothing was dropped."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="skip-clean", email="skip-clean@example.org"
        )
        create_dataset = await _tool(server, "create_dataset")
        create_entity = await _tool(server, "create_entity")
        validate = await _tool(server, "validate_dataset")

        with _calling_with(secret):
            await create_dataset("Clean", "miappe", "1.1")
            await create_entity("Clean", "Investigation", {"identifier": "INV-1"})
            report = json.loads(await validate("Clean"))

        assert not any(issue["rule"] == SKIPPED_NODE_RULE for issue in report["issues"]), report


async def _mutation(server, name: str):
    """One mutating tool, already bound to the arguments its refusal test needs.

    Args:
        server: The MCP server fixture.
        name: The tool to bind.

    Returns:
        A zero-argument coroutine function that performs the mutation on the
        dataset named "Damaged".
    """
    tool = await _tool(server, name)
    arguments: dict[str, tuple[Any, ...]] = {
        "create_entity": ("Damaged", "Investigation", {"identifier": "INV-2"}),
        "update_entity": ("Damaged", "inv-1", {"identifier": "INV-9"}),
        "delete_entity": ("Damaged", "inv-1"),
        "batch_create": ("Damaged", [{"entity_type": "Investigation", "data": {}}]),
    }
    return lambda: tool(*arguments[name])


class TestTheMcpRefusal:
    """No mutating tool may be the one that deletes what did not load.

    Every edit rewrites the whole stored payload from the loaded facade, so an
    edit to an unrelated entity is what destroys the unloadable one. Reporting
    it afterwards is too late, and reporting it beforehand does not help an
    agent that never asked. So the edit is refused while the dataset is in that
    state -- and the refusal has to say enough that the agent can decide, rather
    than leaving it to retry the same call.
    """

    async def _damaged(self, server, session, *, slug: str) -> str:
        """A dataset holding one unloadable node; returns its owner's token."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug=slug, email=f"{slug}@example.org"
        )
        await _dataset_with_an_unloadable_node("Damaged", secret, server)
        return secret

    @pytest.mark.parametrize(
        "tool_name", ["create_entity", "update_entity", "delete_entity", "batch_create"]
    )
    async def test_every_mutating_tool_refuses_and_names_the_loss(
        self, server, session, tool_name: str
    ) -> None:
        secret = await self._damaged(server, session, slug=f"ref-{tool_name[:4]}")
        mutate = await _mutation(server, tool_name)

        with _calling_with(secret), pytest.raises(ValueError) as raised:
            await mutate()

        message = str(raised.value)
        assert "1 stored node" in message, message
        assert "Bogus" in message, message
        assert "storage" in message, message

    async def test_the_refusal_names_the_deliberate_way_through(self, server, session) -> None:
        """An agent told only "no" retries; it has to be told what does work."""
        secret = await self._damaged(server, session, slug="ref-way")
        create_entity = await _tool(server, "create_entity")

        with _calling_with(secret), pytest.raises(ValueError) as raised:
            await create_entity("Damaged", "Investigation", {"identifier": "INV-2"})

        assert "save_dataset" in str(raised.value), str(raised.value)

    async def test_a_damaged_dataset_stays_readable(self, server, session) -> None:
        """Inspecting the damage must not be blocked by it."""
        secret = await self._damaged(server, session, slug="ref-read")
        get_dataset = await _tool(server, "get_dataset")
        list_entities = await _tool(server, "list_entities")
        get_entity = await _tool(server, "get_entity")
        validate = await _tool(server, "validate_dataset")

        with _calling_with(secret):
            stored = json.loads(await get_dataset("Damaged"))
            listed = json.loads(await list_entities("Damaged"))
            entity = json.loads(await get_entity("Damaged", "inv-1"))
            report = json.loads(await validate("Damaged"))

        # The unloadable node is still in storage, which is the whole point.
        assert [n["id"] for n in stored["data"]["tree"]] == ["gone-1", "inv-1"]
        assert [e["id"] for e in listed["entities"]] == ["inv-1"]
        assert entity["entity_type"] == "Investigation"
        assert any(issue["rule"] == SKIPPED_NODE_RULE for issue in report["issues"]), report

    async def test_save_dataset_still_replaces_the_whole_dataset(self, server, session) -> None:
        """The deliberate way through must actually be open, or the refusal traps."""
        from metaseed_hub.database import db

        secret = await self._damaged(server, session, slug="ref-save")
        save = await _tool(server, "save_dataset")
        replacement = {"profile": "miappe", "version": "1.1", "tree": []}

        with _calling_with(secret):
            result = json.loads(await save("Damaged", replacement))

        assert result["saved"] is True
        async with db.session_factory() as check:
            dataset = (
                await check.execute(select(Dataset).where(Dataset.name == "Damaged"))
            ).scalar_one()
        assert dataset.data["tree"] == []

    async def test_a_dataset_that_loads_cleanly_is_never_refused(self, server, session) -> None:
        """The guard that matters: a normal dataset must edit exactly as before."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ref-clean", email="ref-clean@example.org"
        )
        create_dataset = await _tool(server, "create_dataset")
        create_entity = await _tool(server, "create_entity")
        update_entity = await _tool(server, "update_entity")
        delete_entity = await _tool(server, "delete_entity")
        batch_create = await _tool(server, "batch_create")

        with _calling_with(secret):
            await create_dataset("Clean", "miappe", "1.1")
            created = json.loads(
                await create_entity("Clean", "Investigation", {"identifier": "INV-1"})
            )
            await update_entity("Clean", created["id"], {"identifier": "INV-1b"})
            batched = json.loads(
                await batch_create(
                    "Clean", [{"entity_type": "Investigation", "data": {"identifier": "INV-2"}}]
                )
            )
            deleted = json.loads(await delete_entity("Clean", created["id"]))

        assert batched["created"] == 1, batched
        assert deleted["deleted"] == created["id"], deleted


class TestTheWebPanel:
    """A user clicking Validate sees the entities that did not load."""

    async def test_the_validate_route_shows_what_did_not_load(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.dependencies import tenant_slug_for
        from metaseed_hub.ui.routes.dataset.editor import dataset_validate

        subject = "kc-skipweb"
        tenant = make_tenant(slug=tenant_slug_for(subject))
        session.add(tenant)
        await session.flush()
        user = make_user(tenant=tenant, email="skipweb@example.org", keycloak_id=subject)
        session.add(user)
        await session.flush()
        dataset = make_dataset(
            tenant=tenant, profile="miappe", version="1.1", data=_tree_with_an_unloadable_node()
        )
        session.add(dataset)
        await session.commit()

        request = Mock()
        request.cookies = {}
        request.headers = {}
        token = get_or_create_csrf_token(request)
        request.cookies = {"metaseed_csrf_token": token}
        request.headers = {"X-CSRF-Token": token}

        response = await dataset_validate(
            request,
            dataset.id,
            session,
            TokenUser(sub=user.keycloak_id, email=user.email, name="S", roles=[]),
        )
        html = response.body.decode()

        assert "Bogus" in html
        assert "did not load" in html


class TestTheBrowserRefusal:
    """The browser mutation path refuses for the same reason the MCP one does.

    Every web edit goes through ``get_dataset_state_for_mutation`` and is saved
    by rewriting the dataset from the loaded facade, so editing one cell of a
    damaged dataset deletes the nodes that did not load -- exactly the MCP
    defect, on the surface a person uses.
    """

    async def test_the_refusal_speaks_to_someone_without_the_tools(self) -> None:
        """An agent is told which call drops them; a person has no such call."""
        from metaseed import SkippedNode

        from metaseed_hub.ui.helpers.load_report import (
            AGENT_WAY_THROUGH,
            BROWSER_WAY_THROUGH,
            unloadable_node_refusal,
        )

        skipped = [
            SkippedNode(
                entity_type="Study",
                reason="unknown entity type 'Study'",
                node={"id": "n1"},
                descendants_dropped=0,
            )
        ]

        for_agent = unloadable_node_refusal(skipped, AGENT_WAY_THROUGH)
        for_browser = unloadable_node_refusal(skipped, BROWSER_WAY_THROUGH)

        assert "save_dataset" in for_agent
        assert "save_dataset" not in for_browser, (
            "the browser has no save_dataset call; naming it is a dead end"
        )
        assert "version history" in for_browser
        for message in (for_agent, for_browser):
            assert "Study" in message
            assert "would delete them" in message

    async def test_the_mutation_dependency_refuses_what_did_not_load(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A browser edit against a damaged dataset is refused, not silently lossy.

        Asserted through the dependency's behaviour rather than its source: the
        collector moved into ``ensure_dataset_facade_for_write`` and a test that
        grepped for ``on_skip=`` failed on a refactor that changed nothing a
        caller can observe.
        """
        from unittest.mock import AsyncMock

        from fastapi import HTTPException

        from metaseed_hub.access import tenant_slug_for
        from metaseed_hub.ui import dependencies
        from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE

        tenant = make_tenant(slug=tenant_slug_for("mutrefuse-kc"))
        session.add(tenant)
        await session.flush()
        user = make_user(tenant=tenant, keycloak_id="mutrefuse-kc")
        session.add(user)
        await session.flush()
        dataset = make_dataset(tenant=tenant, name="damaged-for-mutation")
        dataset.data = _tree_with_an_unloadable_node()
        flag_modified(dataset, "data")
        session.add(dataset)
        await session.commit()

        token = TokenUser(sub="mutrefuse-kc", email=user.email, name="U", roles=[])
        monkeypatch.setattr(
            dependencies, "get_current_user_from_cookie", AsyncMock(return_value=token)
        )
        csrf = get_or_create_csrf_token(Mock(cookies={}))
        request = Mock()
        request.cookies = {CSRF_TOKEN_COOKIE: csrf}
        request.headers = {"X-CSRF-Token": csrf}

        with pytest.raises(HTTPException) as refusal:
            await dependencies.get_dataset_state_for_mutation(
                request=request, dataset_id=dataset.id, session=session
            )

        assert refusal.value.status_code == 409
        assert "would delete them" in str(refusal.value.detail)


class TestImportRoutesRefuseToo:
    """Three browser routes loaded-then-saved without the refusal, so importing
    into a damaged dataset silently deleted the nodes that did not load —
    the exact loss `get_dataset_state_for_mutation` exists to prevent, reached
    around it."""

    async def _damaged_dataset(self, session: AsyncSession) -> tuple[Dataset, TokenUser]:
        tenant = make_tenant(slug="importdmg")
        session.add(tenant)
        await session.flush()
        user = make_user(tenant=tenant, keycloak_id="importdmg-kc")
        session.add(user)
        await session.flush()
        dataset = make_dataset(tenant=tenant, name="damaged")
        dataset.data = _tree_with_an_unloadable_node()
        flag_modified(dataset, "data")
        session.add(dataset)
        await session.commit()
        token = TokenUser(sub="importdmg-kc", email=user.email, name="U", roles=[])
        return dataset, token

    @pytest.mark.asyncio
    async def test_the_write_load_path_refuses_a_damaged_dataset(
        self, session: AsyncSession
    ) -> None:
        from fastapi import HTTPException

        from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade_for_write

        dataset, _token = await self._damaged_dataset(session)

        with pytest.raises(HTTPException) as caught:
            await ensure_dataset_facade_for_write(dataset, session)

        assert caught.value.status_code == 409
        assert "Bogus" in str(caught.value.detail)

    @pytest.mark.asyncio
    async def test_a_clean_dataset_passes_the_write_load_path(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade_for_write

        tenant = make_tenant(slug="importok")
        session.add(tenant)
        await session.flush()
        dataset = make_dataset(tenant=tenant, name="clean")
        session.add(dataset)
        await session.commit()

        state = await ensure_dataset_facade_for_write(dataset, session)

        assert state is not None

    def test_every_import_route_loads_through_the_refusing_path(self) -> None:
        """The three routes the review caught, held to it structurally: a
        load-then-save route calling the permissive loader is the bug."""
        import inspect

        from metaseed_hub.ui.routes.dataset import crud, editor

        for fn in (
            editor.dataset_import_into_existing,
            crud.dataset_import_source,
            crud.dataset_load_example,
        ):
            source = inspect.getsource(fn)
            assert "ensure_dataset_facade_for_write(" in source, fn.__name__
            assert "ensure_dataset_facade(dataset, session)" not in source, fn.__name__
