"""An MCP endpoint so an agent can work on a user's hub datasets.

The hub authenticates people through OIDC, which needs a browser. An MCP client
is not a browser, so it presents a personal access token instead
(:mod:`metaseed_hub.tokens`), and every tool acts as exactly the user that token
was issued to.

**Why the tools are defined here rather than reused from metaseed.** Every
metaseed MCP tool is a synchronous function, and FastMCP calls sync tools
directly on the event loop -- ``call_fn_with_arg_validation`` ends in
``if fn_is_async: return await fn(...) else: return fn(...)``. A sync tool
therefore cannot await the hub's ``AsyncSession``, and serving several people
from one process means the session must be resolved per call, from the caller's
token. Async tools can await, so these are async, and the metaseed logic they
call is the pure part: spec loading, model building, validation.

Isolation is the property that matters. There is no process-wide session and no
default caller: a request without a usable token fails, because the only thing
left to fall back on would be somebody else's data.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from metaseed_hub.database import db
from metaseed_hub.models import Dataset, Spec, SpecStatus, User
from metaseed_hub.tokens import authenticate_token, token_from_header

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


class NotAuthenticatedError(Exception):
    """The caller could not be identified from its token.

    Raised rather than falling back to any default: a host serving several
    people has nothing to fall back to except another caller's data.
    """


def _bearer_token() -> str | None:
    """The token presented by the call currently being served.

    Read from the SDK's per-request context rather than a ContextVar the host
    sets: the MCP server dispatches each handler from its own task group, so a
    value bound around the HTTP request is not visible inside the tool body.
    """
    from metaseed.agent.mcp.caller import current_request

    request = current_request()
    if request is None:
        return None
    return token_from_header(request.headers.get("authorization"))


async def _caller() -> AsyncIterator[tuple[AsyncSession, User]]:
    """Yield a session and the user the current call acts as.

    Raises:
        NotAuthenticatedError: If no valid, unrevoked token was presented.
    """
    secret = _bearer_token()
    if not secret:
        raise NotAuthenticatedError("No bearer token. Send Authorization: Bearer msh_...")

    async with db.session_factory() as session:
        user = await authenticate_token(session, secret)
        if user is None:
            raise NotAuthenticatedError("That token is not valid, or has been revoked.")
        yield session, user


async def _owned_dataset(session: AsyncSession, user: User, name: str) -> Dataset:
    """A dataset in the caller's own workspace, by name.

    Scoped to the caller's workspace, so a name that exists for somebody else
    reads as absent rather than reachable.
    """
    result = await session.execute(
        select(Dataset).where(
            Dataset.tenant_id == user.tenant_id,
            Dataset.name == name,
            Dataset.deleted_at.is_(None),
        )
    )
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise ValueError(f"No dataset named {name!r} in your workspace")
    return dataset


def create_mcp_server(name: str = "metaseed-hub") -> FastMCP:
    """Build the hub's MCP server.

    Every tool resolves its own caller, so nothing is shared between the people
    the process is serving.
    """
    mcp: FastMCP = FastMCP(
        name=name,
        instructions=(
            "Datasets belonging to one hub user, authenticated by a personal "
            "access token. Call list_profiles and get_profile_schema before "
            "creating entities: each profile defines its own entity types and "
            "fields, and any other type is rejected. Record only what the "
            "source states; leave unknown fields empty."
        ),
        stateless_http=True,
        # Served at the mount root: the app is mounted at /hub/mcp, and the
        # default of "/mcp" would put the endpoint at /hub/mcp/mcp.
        streamable_http_path="/",
        # The deployment sits behind a reverse proxy, so the Host the app sees
        # is not the one the client dialled and the DNS-rebinding check rejects
        # every request with 421. That check defends a *locally bound* server
        # from a browser; this endpoint is authenticated by bearer token on
        # every call, which is what actually protects it.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    async def whoami() -> str:
        """Report which hub account this token acts as."""
        async for _session, user in _caller():
            return json.dumps({"email": user.email, "display_name": user.display_name})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def list_datasets() -> str:
        """List the datasets in the caller's own workspace."""
        async for session, user in _caller():
            result = await session.execute(
                select(Dataset)
                .where(Dataset.tenant_id == user.tenant_id, Dataset.deleted_at.is_(None))
                .order_by(Dataset.updated_at.desc())
            )
            return json.dumps(
                [
                    {"name": d.name, "profile": d.profile, "version": d.version}
                    for d in result.scalars().all()
                ]
            )
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def get_dataset(name: str) -> str:
        """Return a dataset's stored contents.

        Args:
            name: The dataset's name in the caller's workspace.
        """
        async for session, user in _caller():
            dataset = await _owned_dataset(session, user, name)
            return json.dumps(
                {
                    "name": dataset.name,
                    "profile": dataset.profile,
                    "version": dataset.version,
                    "data": dataset.data,
                }
            )
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def create_dataset(name: str, profile: str, version: str) -> str:
        """Create an empty dataset in the caller's workspace.

        Args:
            name: A name unique within the workspace.
            profile: A profile name from list_profiles.
            version: The profile version.
        """
        async for session, user in _caller():
            existing = await session.execute(
                select(Dataset).where(
                    Dataset.tenant_id == user.tenant_id,
                    Dataset.name == name,
                    Dataset.deleted_at.is_(None),
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise ValueError(f"A dataset named {name!r} already exists")

            dataset = Dataset(
                tenant_id=user.tenant_id,
                name=name,
                profile=profile,
                version=version,
                data={},
            )
            session.add(dataset)
            await session.commit()
            return json.dumps({"name": name, "profile": profile, "version": version})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def save_dataset(name: str, data: dict[str, Any]) -> str:
        """Replace a dataset's contents.

        Args:
            name: The dataset to write to, in the caller's workspace.
            data: The full dataset contents, replacing what is stored.
        """
        async for session, user in _caller():
            dataset = await _owned_dataset(session, user, name)
            dataset.data = data
            await session.commit()
            return json.dumps({"name": name, "saved": True})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def list_profiles() -> str:
        """List the metadata standards available, built-in and published.

        Published specifications are included: publishing shares a specification
        with every user of the hub, so any of them can be used here.
        """
        from metaseed.specs.loader import SpecLoader

        built_in = SpecLoader().list_profiles()

        async for session, _user in _caller():
            result = await session.execute(
                select(Spec).where(Spec.status == SpecStatus.PUBLISHED, Spec.deleted_at.is_(None))
            )
            published = [
                {"name": s.name, "version": s.version, "source": "published"}
                for s in result.scalars().all()
            ]
            return json.dumps({"built_in": built_in, "published": published})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def get_profile_schema(profile: str, version: str) -> str:
        """Return a profile's entity types and their fields.

        Call this before creating entities: only the types it names exist.

        Args:
            profile: The profile name.
            version: The profile version.
        """
        from metaseed.specs.loader import SpecLoader

        # Pure: reads the packaged spec, touches no caller state. Still behind
        # authentication, so an unauthenticated caller learns nothing.
        async for _session, _user in _caller():
            spec = SpecLoader(profile=profile).load_profile(version=version, profile=profile)
            return json.dumps(
                {
                    "name": spec.name,
                    "version": spec.version,
                    "root_entity": spec.root_entity,
                    "entities": {
                        entity_name: [f.codename for f in entity.fields]
                        for entity_name, entity in spec.entities.items()
                    },
                }
            )
        raise NotAuthenticatedError("unreachable")

    return mcp
