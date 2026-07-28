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
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from metaseed_hub.database import db
from metaseed_hub.models import (
    Dataset,
    DatasetVersion,
    Spec,
    SpecDraft,
    SpecStatus,
    User,
)
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


logger = logging.getLogger("metaseed_hub")

# An agent can generate a lot of text. A dataset far past this is a mistake or a
# runaway loop, and refusing is kinder than letting it bloat the row.
MAX_DATASET_BYTES = 5 * 1024 * 1024


async def _snapshot(session: AsyncSession, dataset: Dataset, user: User) -> None:
    """Record the dataset's current contents as a version, before overwriting.

    An agent replaces a whole dataset in one call. Without a snapshot that is
    unrecoverable, and the person whose data it is has no way back -- so every
    write through this endpoint leaves the previous state in the same version
    history the web UI shows.
    """
    from sqlalchemy import func

    max_version = (
        await session.execute(
            select(func.coalesce(func.max(DatasetVersion.version_number), 0)).where(
                DatasetVersion.dataset_id == dataset.id
            )
        )
    ).scalar() or 0
    session.add(
        DatasetVersion(
            dataset_id=dataset.id,
            version_number=max_version + 1,
            data=dataset.data,
            created_by_id=user.id,
        )
    )


async def _validation_report(session: AsyncSession, dataset: Dataset) -> dict[str, Any]:
    """What is wrong or missing in a dataset, in terms an agent can act on.

    Structured issues plus one instruction, because a bare ``valid: false``
    tells an agent nothing about what to do next -- and an agent that believes
    it has finished stops, leaving a half-filled dataset that was reported as
    saved.
    """
    from metaseed_hub.ui.helpers import ensure_dataset_facade

    state = await ensure_dataset_facade(dataset, session)
    if state.facade is None:
        return {
            "valid": False,
            "issues": [],
            "next_step": (
                f"The profile {dataset.profile!r} could not be loaded, so nothing "
                "was checked. Confirm it exists with list_profiles."
            ),
        }

    from metaseed import MetaseedClient

    # ValidationResult, not a sequence: iterating it raises TypeError, so the
    # issues are read from .issues. (bool(result) is valid/invalid correctly.)
    result = MetaseedClient.from_facade(state.facade).validate()
    issues = list(result.issues)
    # Passed through as metaseed reports them. The issues are already derived
    # from the spec -- which field is required, which relationship needs a
    # minimum -- so re-deriving any of it here would only let the two disagree.
    # `rule` names the spec rule that failed and is the most actionable part.
    reported = [
        {
            "entity_id": i.entity_id,
            "field": i.field,
            "rule": i.rule,
            "message": i.message,
        }
        for i in issues
    ]

    if not reported:
        return {"valid": True, "issues": [], "next_step": "Nothing is missing."}

    # Grouped by the spec rule that failed, so an agent sees "3 required fields
    # missing" rather than a flat list it has to re-read.
    by_rule: dict[str, int] = {}
    for issue in reported:
        by_rule[issue["rule"]] = by_rule.get(issue["rule"], 0) + 1
    summary = ", ".join(f"{count} x {rule}" for rule, count in sorted(by_rule.items()))
    next_step = (
        f"{len(reported)} item(s) still need attention ({summary}). Fill these "
        "from the source. Leave a field empty rather than inventing a value; an "
        "empty required field is a smaller problem than a wrong one."
    )
    return {"valid": False, "issues": reported, "next_step": next_step}


@asynccontextmanager
async def _editing(session: AsyncSession, dataset: Dataset, user: User) -> AsyncIterator[Any]:
    """Yield a client over ``dataset``, then persist what the block changed.

    The envelope every editing tool needs, and the only async part: metaseed's
    entity operations are pure and in-memory, so the hub loads, hands them the
    client, and writes back. Wrapping it once means no tool can forget the
    snapshot or the size check.
    """
    from metaseed import MetaseedClient
    from sqlalchemy.orm.attributes import flag_modified

    from metaseed_hub.ui.helpers import ensure_dataset_facade

    state = await ensure_dataset_facade(dataset, session)
    if state.facade is None:
        raise ValueError(
            f"The profile {dataset.profile!r} could not be loaded, so this "
            "dataset cannot be edited. Check it with list_profiles."
        )

    client = MetaseedClient.from_facade(state.facade)
    yield client

    data = client.serialize()
    size = len(json.dumps(data).encode())
    if size > MAX_DATASET_BYTES:
        raise ValueError(
            f"That edit takes the dataset to {size} bytes; the limit is {MAX_DATASET_BYTES}."
        )
    if data != dataset.data:
        await _snapshot(session, dataset, user)
    dataset.data = data
    flag_modified(dataset, "data")
    await session.commit()


async def _owned_draft(session: AsyncSession, user: User, name: str) -> SpecDraft:
    """A spec draft in the caller's own workspace, by name."""
    result = await session.execute(
        select(SpecDraft).where(SpecDraft.tenant_id == user.tenant_id, SpecDraft.name == name)
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise ValueError(f"No specification draft named {name!r} in your workspace")
    return draft


@asynccontextmanager
async def _building(session: AsyncSession, draft: SpecDraft, user: User) -> AsyncIterator[Any]:
    """Yield a SpecBuilder over ``draft``, then persist what the block changed.

    ``SpecBuilder`` is metaseed's, pure, and already shared by the web UI: the
    hub adds only loading and saving. Drafts are written, never published specs
    -- publishing shares a specification with every user, so it stays a human
    action.
    """
    from metaseed_hub.ui.spec_builder.state import SpecBuilderState

    state = SpecBuilderState.from_dict(draft.spec_data) if draft.spec_data else SpecBuilderState()
    if state.spec is None:
        raise ValueError(f"Draft {draft.name!r} holds no specification to edit")

    from metaseed.specs.builder import SpecBuilder

    builder = SpecBuilder.from_spec(state.spec)
    yield builder

    state.spec = builder.spec
    draft.spec_data = state.to_dict()
    draft.version = builder.spec.version
    await session.commit()


def _allowed_hosts() -> list[str]:
    """The Host header values the MCP endpoint accepts, from ``app_url``.

    A client dials the deployment's own host, which the reverse proxy forwards
    unchanged, so the check must allow exactly that. Derived from the setting so
    it is right in every environment rather than hardcoded to production, and
    with the bare hostname included as well, since a default port is dropped
    from the Host header.
    """
    from urllib.parse import urlparse

    from metaseed_hub.config import get_settings

    netloc = urlparse(get_settings().app_url).netloc
    hosts = [netloc]
    hostname = netloc.split(":", 1)[0]
    if hostname and hostname != netloc:
        hosts.append(hostname)
    return hosts


def create_mcp_server(name: str = "metaseed-hub") -> FastMCP:
    """Build the hub's MCP server.

    Every tool resolves its own caller, so nothing is shared between the people
    the process is serving.
    """
    mcp: FastMCP = FastMCP(
        name=name,
        instructions=(
            "Datasets and specifications belonging to one hub user, "
            "authenticated by a personal access token.\n\n"
            "Populating a dataset: call list_profiles, then get_profile_schema "
            "for the profile you are using. Only the entity types it names "
            "exist, and any other is rejected. Build the dataset with "
            "create_entity and update_entity one entity at a time rather than "
            "save_dataset, which replaces the whole dataset and overwrites "
            "anything changed meanwhile. Every edit reports what is still "
            "missing; work through that until validate_dataset is clean.\n\n"
            "Record only what the source states. Leave a field empty rather "
            "than inventing a value: an empty required field is a smaller "
            "problem than a wrong one, and it is reported so it can be filled "
            "later.\n\n"
            "Building a specification: spec_create makes a private draft, then "
            "spec_add_entity and spec_add_field build it up and spec_validate "
            "reports problems. Publishing is deliberately not available here — "
            "it shares a specification with every user of the hub, so the "
            "person must do it themselves in the web interface."
        ),
        stateless_http=True,
        # Served at the mount root: the app is mounted at /hub/mcp, and the
        # default of "/mcp" would put the endpoint at /hub/mcp/mcp.
        streamable_http_path="/",
        # DNS-rebinding protection restricts which Host header the endpoint
        # accepts. The default allowlist is empty, which behind the reverse
        # proxy rejected every request with 421 — the proxy forwards the public
        # Host, not one the check knew about. Rather than disable the check, pin
        # it to the deployment's own host (derived from app_url so dev and prod
        # each get the right value). Bearer tokens are the real protection here;
        # this is defence in depth on the Host header.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts(),
        ),
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

        The previous contents are kept as a version, so a mistaken overwrite can
        be restored from the dataset's history in the web interface.

        Args:
            name: The dataset to write to, in the caller's workspace.
            data: The full dataset contents, replacing what is stored.
        """
        from sqlalchemy.orm.attributes import flag_modified

        size = len(json.dumps(data).encode())
        if size > MAX_DATASET_BYTES:
            raise ValueError(f"That dataset is {size} bytes; the limit is {MAX_DATASET_BYTES}.")

        async for session, user in _caller():
            dataset = await _owned_dataset(session, user, name)
            if data != dataset.data:
                await _snapshot(session, dataset, user)
            dataset.data = data
            # JSONB is mutable in place; without this the assignment can be
            # missed and the write silently does nothing.
            flag_modified(dataset, "data")
            await session.commit()
            logger.info("mcp: %s saved dataset %r (%d bytes)", user.email, name, size)

            # Validated after the write, not before: a partially complete
            # dataset is a normal intermediate state and refusing it would stop
            # an agent working incrementally. Reporting what is still missing is
            # what lets it finish the job instead of believing it is done.
            report = await _validation_report(session, dataset)
            return json.dumps({"name": name, "saved": True, "bytes": size, **report})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def delete_dataset(name: str) -> str:
        """Remove a dataset from the caller's workspace.

        Soft: the dataset stops being listed but is not erased, so an agent
        cannot destroy someone's work irrecoverably.

        Args:
            name: The dataset to remove.
        """
        async for session, user in _caller():
            dataset = await _owned_dataset(session, user, name)
            dataset.soft_delete()
            await session.commit()
            logger.info("mcp: %s deleted dataset %r", user.email, name)
            return json.dumps({"name": name, "deleted": True})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def validate_dataset(name: str) -> str:
        """Check a dataset against its profile and report what is missing.

        Args:
            name: The dataset to validate, in the caller's workspace.
        """
        async for session, user in _caller():
            dataset = await _owned_dataset(session, user, name)
            report = await _validation_report(session, dataset)
            return json.dumps({"name": name, **report})
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
                    "guidance": (
                        f"Start from the root entity {spec.root_entity!r}. Only "
                        "the entity types listed here exist; any other is "
                        "rejected. Fill the fields marked required, and leave "
                        "anything the source does not state empty rather than "
                        "inventing a value."
                    ),
                    "entities": {
                        entity_name: {
                            "description": entity.description,
                            # Required first: it is the order the fields should
                            # be filled in, and the ones an agent must not skip.
                            "fields": sorted(
                                (
                                    {
                                        "codename": f.codename,
                                        "name": f.name,
                                        "type": f.type,
                                        "required": bool(f.required),
                                        "description": f.description,
                                        "ontology_term": f.ontology_term,
                                    }
                                    for f in entity.fields
                                ),
                                key=lambda f: (not f["required"], f["codename"]),
                            ),
                        }
                        for entity_name, entity in spec.entities.items()
                    },
                }
            )
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def create_entity(
        dataset: str,
        entity_type: str,
        data: dict[str, Any],
        parent_id: str | None = None,
    ) -> str:
        """Add one entity to a dataset, without rewriting the rest.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_type: A type from get_profile_schema. Any other is rejected.
            data: Field values, keyed by the field's codename.
            parent_id: The entity this one belongs under, where the profile
                nests them. Omit for a root entity.
        """
        async for session, user in _caller():
            row = await _owned_dataset(session, user, dataset)
            async with _editing(session, row, user) as client:
                # skip_validation, because a partially filled entity is a normal
                # intermediate state; what is missing is reported below rather
                # than refused, so an agent can build a dataset in steps.
                entity = client.create_entity(
                    entity_type, data, parent_id=parent_id, skip_validation=True
                )
                created = {"id": entity.id, "entity_type": entity.entity_type}
            report = await _validation_report(session, row)
            logger.info("mcp: %s created %s in %r", user.email, entity_type, dataset)
            return json.dumps({**created, **report})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def update_entity(dataset: str, entity_id: str, data: dict[str, Any]) -> str:
        """Change field values on one entity, leaving the rest of it alone.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_id: The entity to change, from list_entities.
            data: The fields to set. Fields not named keep their values.
        """
        async for session, user in _caller():
            row = await _owned_dataset(session, user, dataset)
            async with _editing(session, row, user) as client:
                # Merged, not replaced. metaseed's update_entity overwrites the
                # entity's data wholesale, so passing one field would silently
                # drop every other value -- and an agent setting one field at a
                # time would destroy its own earlier work.
                merged = {**client.get_entity(entity_id).data, **data}
                entity = client.update_entity(entity_id, merged, skip_validation=True)
                updated = {"id": entity.id, "entity_type": entity.entity_type}
            report = await _validation_report(session, row)
            logger.info("mcp: %s updated %s in %r", user.email, entity_id, dataset)
            return json.dumps({**updated, **report})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def delete_entity(dataset: str, entity_id: str) -> str:
        """Remove one entity from a dataset.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_id: The entity to remove.
        """
        async for session, user in _caller():
            row = await _owned_dataset(session, user, dataset)
            async with _editing(session, row, user) as client:
                client.delete_entity(entity_id)
            report = await _validation_report(session, row)
            logger.info("mcp: %s deleted entity %s in %r", user.email, entity_id, dataset)
            return json.dumps({"deleted": entity_id, **report})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def list_entities(dataset: str, entity_type: str | None = None) -> str:
        """List a dataset's entities, with their ids and labels.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_type: Restrict to one type. Omit for all of them.
        """
        async for session, user in _caller():
            row = await _owned_dataset(session, user, dataset)
            entities = [
                {
                    "id": e.get("_node_id"),
                    "entity_type": e.get("_type"),
                    "data": {k: v for k, v in e.items() if not k.startswith("_")},
                }
                for e in (row.data or {}).get("entities", [])
                if isinstance(e, dict)
            ]
            if entity_type:
                entities = [e for e in entities if e["entity_type"] == entity_type]
            return json.dumps({"dataset": dataset, "entities": entities})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def get_entity(dataset: str, entity_id: str) -> str:
        """Return one entity's stored field values.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_id: The entity to read.
        """
        async for session, user in _caller():
            row = await _owned_dataset(session, user, dataset)
            for entity in (row.data or {}).get("entities", []):
                if isinstance(entity, dict) and entity.get("_node_id") == entity_id:
                    return json.dumps(
                        {
                            "id": entity_id,
                            "entity_type": entity.get("_type"),
                            "data": {k: v for k, v in entity.items() if not k.startswith("_")},
                        }
                    )
            raise ValueError(f"No entity {entity_id!r} in {dataset!r}")
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def spec_create(name: str, version: str, description: str = "") -> str:
        """Start a new specification as a private draft.

        A draft is visible only to you. Publishing it — which shares it with
        every user of the hub — is done from the web interface, deliberately:
        it is not something an agent should do on your behalf.

        Args:
            name: The profile name.
            version: The profile version, e.g. "1.0".
            description: What the specification is for.
        """
        from metaseed.specs.builder import SpecBuilder

        from metaseed_hub.ui.spec_builder.access import create_new_draft

        async for session, user in _caller():
            existing = await session.execute(
                select(SpecDraft).where(
                    SpecDraft.tenant_id == user.tenant_id, SpecDraft.name == name
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise ValueError(f"A draft named {name!r} already exists")

            builder = SpecBuilder.empty(name, version, description=description)
            draft = await create_new_draft(
                session,
                user_id=user.id,
                tenant_id=user.tenant_id,
                name=name,
                spec=builder.spec,
            )
            logger.info("mcp: %s created spec draft %r", user.email, name)
            return json.dumps({"name": draft.name, "version": draft.version})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def spec_add_entity(
        draft: str, entity: str, description: str = "", ontology_term: str | None = None
    ) -> str:
        """Add an entity type to a draft specification.

        Args:
            draft: The draft's name in the caller's workspace.
            entity: The entity type's name, e.g. "Study".
            description: What the entity represents.
            ontology_term: An ontology term identifying it, where one applies.
        """
        async for session, user in _caller():
            row = await _owned_draft(session, user, draft)
            async with _building(session, row, user) as builder:
                builder.add_entity(entity, description=description, ontology_term=ontology_term)
                problems = builder.validate()
            return json.dumps({"entity": entity, "problems": problems})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def spec_add_field(
        draft: str,
        entity: str,
        field: str,
        field_type: str,
        required: bool = False,
        description: str = "",
        ontology_term: str | None = None,
    ) -> str:
        """Add a field to an entity in a draft specification.

        Args:
            draft: The draft's name in the caller's workspace.
            entity: The entity type to add the field to.
            field: The field's name.
            field_type: One of string, integer, float, boolean, date, datetime,
                uri, ontology_term, list, entity.
            required: Whether a dataset must supply it.
            description: What the field records.
            ontology_term: An ontology term identifying it, where one applies.
        """
        async for session, user in _caller():
            row = await _owned_draft(session, user, draft)
            async with _building(session, row, user) as builder:
                builder.add_field(
                    entity,
                    field,
                    field_type,
                    required=required,
                    description=description,
                    ontology_term=ontology_term,
                )
                problems = builder.validate()
            return json.dumps({"entity": entity, "field": field, "problems": problems})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def spec_set_root_entity(draft: str, entity: str) -> str:
        """Set which entity a dataset of this profile starts from.

        Args:
            draft: The draft's name in the caller's workspace.
            entity: The entity type to use as the root.
        """
        async for session, user in _caller():
            row = await _owned_draft(session, user, draft)
            async with _building(session, row, user) as builder:
                builder.set_root_entity(entity)
                problems = builder.validate()
            return json.dumps({"root_entity": entity, "problems": problems})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def spec_validate(draft: str) -> str:
        """Report what is wrong or missing in a draft specification.

        Args:
            draft: The draft's name in the caller's workspace.
        """
        from metaseed.specs.builder import SpecBuilder

        from metaseed_hub.ui.spec_builder.state import SpecBuilderState

        async for session, user in _caller():
            row = await _owned_draft(session, user, draft)
            state = (
                SpecBuilderState.from_dict(row.spec_data) if row.spec_data else SpecBuilderState()
            )
            if state.spec is None:
                return json.dumps(
                    {"draft": draft, "problems": ["The draft holds no specification"]}
                )
            problems = SpecBuilder.from_spec(state.spec).validate()
            return json.dumps({"draft": draft, "valid": not problems, "problems": problems})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def spec_preview_yaml(draft: str) -> str:
        """Return a draft specification as YAML, without saving anything.

        Args:
            draft: The draft's name in the caller's workspace.
        """
        from metaseed.specs.builder import SpecBuilder

        from metaseed_hub.ui.spec_builder.state import SpecBuilderState

        async for session, user in _caller():
            row = await _owned_draft(session, user, draft)
            state = (
                SpecBuilderState.from_dict(row.spec_data) if row.spec_data else SpecBuilderState()
            )
            if state.spec is None:
                raise ValueError(f"Draft {draft!r} holds no specification")
            return json.dumps({"draft": draft, "yaml": SpecBuilder.from_spec(state.spec).to_yaml()})
        raise NotAuthenticatedError("unreachable")

    @mcp.tool()
    async def list_spec_drafts() -> str:
        """List the caller's draft specifications."""
        async for session, user in _caller():
            result = await session.execute(
                select(SpecDraft)
                .where(SpecDraft.tenant_id == user.tenant_id)
                .order_by(SpecDraft.updated_at.desc())
            )
            return json.dumps(
                [{"name": d.name, "version": d.version} for d in result.scalars().all()]
            )
        raise NotAuthenticatedError("unreachable")

    return mcp
