"""A profile name means the same specification to every MCP tool.

Two tenants may publish a specification under the same name and version.
`_published_spec` takes `prefer_tenant` so the caller's own publication wins
that collision — its docstring says exactly that — and `create_dataset` and
`get_profile_schema` pass it.

`get_profile_relationships` and `spec_clone` did not, so they fell through to
the oldest publication across all tenants. An agent following the documented
workflow could call `get_profile_schema` and `get_profile_relationships` on the
same name and version and be handed two different specifications; `spec_clone`
could copy another tenant's spec while `create_dataset` bound the caller's own.

The disagreement was possible because the resolver's type had no slot for the
tenant, so no caller could pass one even if it wanted to.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from metaseed_hub.mcp._profile_tools import register_profile_tools
from metaseed_hub.mcp._spec_tools import register_spec_tools


class _RecordingResolver:
    """A profile_spec that remembers how it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        session: Any,
        profile: str,
        version: str,
        *,
        prefer_tenant: str | None = None,
    ) -> Any:
        self.calls.append({"profile": profile, "version": version, "prefer_tenant": prefer_tenant})
        raise _ResolverObservedError


class _ResolverObservedError(Exception):
    """Raised once the resolver has been observed; the rest is not under test."""


class _User:
    id = "u-1"
    tenant_id = "tenant-of-the-caller"


class _FakeMCP:
    """Collects the functions a register_* call decorates."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorate(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorate


@asynccontextmanager
async def _caller() -> Any:
    yield (object(), _User())


async def _unused(*args: Any, **kwargs: Any) -> Any:
    """A collaborator these two tools do not reach before the resolver."""
    raise AssertionError("not part of this test")


@pytest.mark.asyncio
async def test_get_profile_relationships_prefers_the_callers_tenant() -> None:
    resolver = _RecordingResolver()
    mcp = _FakeMCP()
    register_profile_tools(mcp, caller=_caller, profile_spec=resolver)

    with pytest.raises(_ResolverObservedError):
        await mcp.tools["get_profile_relationships"]("miappe", "1.1")

    assert resolver.calls[0]["prefer_tenant"] == "tenant-of-the-caller"


@pytest.mark.asyncio
async def test_spec_clone_prefers_the_callers_tenant() -> None:
    resolver = _RecordingResolver()
    mcp = _FakeMCP()
    register_spec_tools(
        mcp,
        caller=_caller,
        owned_draft=_unused,
        building=_unused,
        profile_spec=resolver,
    )

    with pytest.raises(_ResolverObservedError):
        await mcp.tools["spec_clone"]("miappe", "1.1")

    assert resolver.calls[0]["prefer_tenant"] == "tenant-of-the-caller"
