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
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

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


class TestTheMcpReport:
    """An agent asking whether a dataset is complete is told what did not load."""

    async def _dataset_with_an_unloadable_node(self, name: str, secret: str, server) -> None:
        """Create a dataset over MCP and give it a payload with one bad node."""
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

    async def test_the_report_carries_the_skipped_node(self, server, session) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="skip-mcp", email="skip-mcp@example.org"
        )
        await self._dataset_with_an_unloadable_node("Lossy", secret, server)
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
