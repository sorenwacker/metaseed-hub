"""Helpers shared by the MCP tool tests.

Every MCP test resolves a caller from a bearer token and reaches a registered
tool by name, so those two steps live here rather than in whichever test module
happened to need them first. The ``server`` fixture they pair with is in
tests/conftest.py, so no test module has to import from another test module.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.tokens import issue_token
from tests.factories import make_tenant, make_user


class _Request:
    """Just the part of a Starlette request the resolver reads."""

    def __init__(self, authorization: str | None) -> None:
        self.headers = {"authorization": authorization} if authorization else {}


@contextmanager
def _calling_with(token: str | None):
    """Serve a tool call as the holder of ``token``.

    Patches the SDK's per-request lookup, which is how a tool body identifies
    its caller under the HTTP transport.
    """
    request = _Request(f"Bearer {token}" if token else None)
    with patch("metaseed.agent.mcp.caller.current_request", lambda: request):
        yield


async def _tool(server, name: str):
    """The registered callable for a tool, by name."""
    tools = await server.list_tools()
    assert any(t.name == name for t in tools), f"{name} is not registered"
    return server._tool_manager.get_tool(name).fn


async def _user_with_token(session: AsyncSession, *, slug: str, email: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=email)
    session.add(user)
    await session.commit()
    secret, token = await issue_token(session, user, name="agent")
    return tenant, user, secret, token


def _tree_nodes(data: dict) -> list[dict]:
    """Flatten a stored tree envelope into its nodes, parents before children.

    The hub stores dataset.data as {profile, version, tree: [...]}; reading the
    tree here asserts the format the web UI consumes, not merely what the MCP
    tools echo back.
    """
    nodes: list[dict] = []

    def walk(node: dict) -> None:
        nodes.append(node)
        for child in node.get("children", []):
            walk(child)

    for root in data.get("tree", []):
        walk(root)
    return nodes


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
