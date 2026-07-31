"""The MCP endpoint itself: where it is mounted, and who it acts as.

One process serves every MCP caller, so the failure that matters is a tool
acting as the wrong person -- or as anyone at all without a token. These tests
drive the tool bodies with a token in scope, because that is where the caller is
resolved; a test that called the underlying queries directly would not exercise
the part that can leak. The tools those callers reach are covered per family in
the sibling test_mcp_*.py modules.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.mcp import NotAuthenticatedError
from metaseed_hub.tokens import revoke_token
from tests.mcp_helpers import _calling_with, _tool, _user_with_token


async def test_the_endpoint_is_mounted_ahead_of_the_hub(server) -> None:
    """/hub matches first if the order is wrong, and the endpoint disappears."""
    from metaseed_hub.main import create_app

    paths = [getattr(r, "path", "") for r in create_app().routes]
    assert "/hub/mcp" in paths
    assert paths.index("/hub/mcp") < paths.index("/hub")


async def test_a_call_without_a_token_is_refused(server, session) -> None:
    """No default caller: the only thing to fall back on is someone else."""
    whoami = await _tool(server, "whoami")

    with _calling_with(None), pytest.raises(NotAuthenticatedError):
        await whoami()


async def test_a_revoked_token_stops_working(server, session: AsyncSession) -> None:
    _t, _u, secret, token = await _user_with_token(session, slug="mcp00001", email="a@example.org")
    await revoke_token(session, token)

    whoami = await _tool(server, "whoami")
    with _calling_with(secret), pytest.raises(NotAuthenticatedError):
        await whoami()


async def test_a_tool_acts_as_the_token_holder(server, session: AsyncSession) -> None:
    """The property the whole design exists for."""
    _ta, _ua, secret_a, _a = await _user_with_token(
        session, slug="mcp00002", email="alice@example.org"
    )
    _tb, _ub, secret_b, _b = await _user_with_token(
        session, slug="mcp00003", email="bob@example.org"
    )
    whoami = await _tool(server, "whoami")

    with _calling_with(secret_a):
        assert json.loads(await whoami())["email"] == "alice@example.org"
    with _calling_with(secret_b):
        assert json.loads(await whoami())["email"] == "bob@example.org"


class TestOverHTTP:
    """Driving the endpoint the way a client does.

    The tool-level tests above call the functions directly, so they passed while
    the endpoint itself was unreachable: it was mounted at /hub/mcp but FastMCP
    serves at its own ``streamable_http_path``, putting the real path at
    /hub/mcp/mcp, and a mounted sub-app's lifespan never ran so the session
    manager was uninitialised. Both produced a broken endpoint under a green
    suite.
    """

    @staticmethod
    def _initialize() -> dict:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }

    def test_the_endpoint_answers_at_hub_mcp(self) -> None:
        """Not /hub/mcp/mcp, and not a 500 from an unstarted session manager."""
        from fastapi.testclient import TestClient

        from metaseed_hub.main import create_app

        # The Host header is now checked against the deployment's host, so the
        # client must dial it (TestClient otherwise sends Host: testserver).
        with TestClient(create_app(), base_url="http://localhost:7001") as client:
            response = client.post(
                "/hub/mcp",
                json=self._initialize(),
                headers={"Accept": "application/json, text/event-stream"},
            )

        assert response.status_code != 404, "the endpoint is not where it is mounted"
        assert response.status_code < 500, f"server error: {response.text[:300]}"

    def test_a_request_with_a_foreign_host_is_rejected(self) -> None:
        """DNS-rebinding defence: only the deployment\'s own Host is accepted.

        A page on evil.example that rebinds DNS to the server would send its own
        Host header; the check refuses it. Bearer tokens are the real barrier —
        this is defence in depth on the Host.
        """
        from fastapi.testclient import TestClient

        from metaseed_hub.main import create_app

        with TestClient(create_app(), base_url="http://evil.example") as client:
            response = client.post(
                "/hub/mcp",
                json=self._initialize(),
                headers={"Accept": "application/json, text/event-stream"},
            )

        assert response.status_code == 421, (
            f"a foreign Host must be refused, got {response.status_code}"
        )

    def test_the_doubled_path_is_not_the_endpoint(self) -> None:
        """Guards the regression directly: /hub/mcp/mcp must not be where it lives."""
        from fastapi.testclient import TestClient

        from metaseed_hub.main import create_app

        with TestClient(create_app(), base_url="http://localhost:7001") as client:
            response = client.post(
                "/hub/mcp/mcp",
                json=self._initialize(),
                headers={"Accept": "application/json, text/event-stream"},
            )

        assert response.status_code == 404
