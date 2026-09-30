"""Every MCP tool that takes a profile name resolves it for the caller.

A specification published to a collaboration is visible to its members. That
rule was applied by ``list_profiles`` and ``get_profile_schema`` (they passed
the caller to the resolver) but not by ``get_profile_relationships``,
``spec_clone`` or the published branch of ``create_dataset``, because the
resolver's contract had no slot for the caller and the create branch had its
own copy of the lookup. An agent was listed a specification, read its schema,
and was then told "No profile named ... Call list_profiles for what exists" by
the next tool.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.collaborations import record_memberships
from metaseed_hub.tokens import issue_token
from tests.factories import make_spec, make_tenant, make_user
from tests.mcp_helpers import _calling_with, _tool

pytestmark = pytest.mark.asyncio

GROUP = "urn:mace:surf.nl:sram:group:tudelft:cropxr:phenotyping"
OTHER = "urn:mace:surf.nl:sram:group:tudelft:other:members"


def _payload(name: str) -> dict:
    return {
        "spec": {
            "name": name,
            "display_name": name,
            "version": "1.0",
            "root_entity": "Sample",
            "entities": {"Sample": {"description": "a sample", "fields": []}},
        }
    }


async def _person(session: AsyncSession, urns: list[str]) -> str:
    """A signed-in person with the given group memberships; returns their token."""
    sub = f"mcpvis-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=sub)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(user)
    await session.flush()
    await record_memberships(session, user.id, urns)
    await session.commit()
    secret, _token = await issue_token(session, user, name="agent")
    return secret


async def _spec_published_to(session: AsyncSession, audience: str, name: str) -> None:
    tenant = make_tenant(slug=f"author-{uuid4().hex[:6]}")
    session.add(tenant)
    await session.flush()
    author = make_user(tenant=tenant, email=f"{tenant.slug}@example.org")
    session.add(author)
    await session.flush()
    spec = make_spec(
        tenant=tenant, created_by=author, name=name, version="1.0", spec_data=_payload(name)
    )
    spec.audience_urn = audience
    session.add(spec)
    await session.commit()


async def _profile_tools(server) -> dict:
    return {
        name: await _tool(server, name)
        for name in (
            "list_profiles",
            "get_profile_schema",
            "get_profile_relationships",
            "spec_clone",
            "create_dataset",
        )
    }


async def test_a_member_sees_a_collaboration_spec_in_every_tool(server, session) -> None:
    name = f"collab-{uuid4().hex[:6]}"
    await _spec_published_to(session, GROUP, name)
    member = await _person(session, [GROUP])
    tools = await _profile_tools(server)

    with _calling_with(member):
        listed = json.loads(await tools["list_profiles"]())
        assert any(p["name"] == name for p in listed["published"]), listed
        assert json.loads(await tools["get_profile_schema"](name, "1.0"))
        assert json.loads(await tools["get_profile_relationships"](name, "1.0"))
        assert json.loads(await tools["spec_clone"](name, "1.0", "my-copy"))["name"] == "my-copy"
        created = json.loads(await tools["create_dataset"]("ds-on-collab", name, "1.0"))
    assert created["profile"] == name.lower()


async def test_an_outsider_sees_it_in_none(server, session) -> None:
    name = f"collab-{uuid4().hex[:6]}"
    await _spec_published_to(session, GROUP, name)
    outsider = await _person(session, [OTHER])
    tools = await _profile_tools(server)

    with _calling_with(outsider):
        listed = json.loads(await tools["list_profiles"]())
        assert not any(p["name"] == name for p in listed["published"])
        for call in (
            lambda: tools["get_profile_schema"](name, "1.0"),
            lambda: tools["get_profile_relationships"](name, "1.0"),
            lambda: tools["spec_clone"](name, "1.0", "my-copy"),
            lambda: tools["create_dataset"]("ds-on-collab", name, "1.0"),
        ):
            with pytest.raises(ValueError, match="No profile named"):
                await call()
