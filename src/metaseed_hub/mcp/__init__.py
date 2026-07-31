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
from metaseed.agent.mcp.server import SPEC_BUILDING_INSTRUCTIONS
from sqlalchemy import select

from metaseed_hub.database import db
from metaseed_hub.mcp._ontology_tools import register_ontology_tools
from metaseed_hub.mcp._profile_tools import register_profile_tools
from metaseed_hub.mcp._spec_tools import register_spec_tools
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


@asynccontextmanager
async def _caller() -> AsyncIterator[tuple[AsyncSession, User]]:
    """Yield a session and the user the current call acts as.

    A context manager rather than a bare generator, so returning from the tool
    body closes the session deterministically instead of leaving it to the
    event loop's async-generator finalization.

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
    from metaseed import SkippedNode

    from metaseed_hub.ui.helpers import ensure_dataset_facade
    from metaseed_hub.ui.helpers.load_report import SKIPPED_NODE_NEXT_STEP, skipped_node_issues
    from metaseed_hub.ui.helpers.spec_hash import DRIFT_RULE, spec_drift_message

    skipped: list[SkippedNode] = []
    state = await ensure_dataset_facade(dataset, session, on_skip=skipped.append)
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

    # Provenance, not a validation failure: the dataset is exactly as valid as
    # metaseed judged it. Reported alongside the issues because it is usually
    # the explanation for them -- but it never flips `valid`, or an agent would
    # start "fixing" a dataset that has nothing wrong with it.
    valid = not reported
    drift = await spec_drift_message(session, dataset)
    if drift is not None:
        reported.append({"entity_id": None, "field": None, "rule": DRIFT_RULE, "message": drift})

    # A node that did not load is not a validation failure either -- the
    # validator never saw it. It does make `valid` false all the same: what was
    # checked is a subset of what is stored, so vouching for the dataset would
    # be vouching for something other than what the agent asked about.
    reported.extend(skipped_node_issues(skipped))
    valid = valid and not skipped

    if not reported:
        return {"valid": True, "issues": [], "next_step": "Nothing is missing."}

    if valid:
        # Only the drift. Nothing to fill in, so saying so would send an agent
        # looking for missing values that are not missing.
        return {
            "valid": True,
            "issues": reported,
            "next_step": (
                "Nothing is missing. The specification has changed since this "
                "dataset was written, though; re-save it to record the current one."
            ),
        }

    # Grouped by the spec rule that failed, so an agent sees "3 required fields
    # missing" rather than a flat list it has to re-read.
    by_rule: dict[str, int] = {}
    for issue in reported:
        by_rule[issue["rule"]] = by_rule.get(issue["rule"], 0) + 1
    summary = ", ".join(f"{count} x {rule}" for rule, count in sorted(by_rule.items()))
    next_step = f"{len(reported)} item(s) still need attention ({summary})."
    if issues:
        next_step += (
            " Fill these from the source. Leave a field empty rather than "
            "inventing a value; an empty required field is a smaller problem "
            "than a wrong one."
        )
    if skipped:
        next_step += SKIPPED_NODE_NEXT_STEP
    if drift is not None:
        next_step += (
            " The spec_drift item is not one of them: it says the specification "
            "changed after this dataset was written, which is often why the rest "
            "appeared."
        )
    return {"valid": False, "issues": reported, "next_step": next_step}


async def _loaded_client(session: AsyncSession, dataset: Dataset) -> Any:
    """A ``MetaseedClient`` holding the dataset's stored entities.

    Loads through ``ensure_dataset_facade``, the same loader the web UI uses,
    so the tree format the hub stores and the legacy flat format both read
    correctly, and built-in, draft-spec, and published-spec datasets all
    resolve their profile the same way.
    """
    from metaseed import MetaseedClient

    from metaseed_hub.ui.helpers import ensure_dataset_facade

    state = await ensure_dataset_facade(dataset, session)
    if state.facade is None:
        raise ValueError(
            f"The profile {dataset.profile!r} could not be loaded, so this "
            "dataset cannot be used. Check it with list_profiles."
        )
    return MetaseedClient.from_facade(state.facade)


@asynccontextmanager
async def _editing(session: AsyncSession, dataset: Dataset, user: User) -> AsyncIterator[Any]:
    """Yield a client over ``dataset``, then persist what the block changed.

    The envelope every editing tool needs, and the only async part: metaseed's
    entity operations are pure and in-memory, so the hub loads, hands them the
    client, and writes back. Wrapping it once means no tool can forget the
    snapshot or the size check.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from metaseed_hub.ui.helpers import make_json_serializable
    from metaseed_hub.ui.helpers.spec_hash import dataset_spec_hash, stamp_spec_hash

    client = await _loaded_client(session, dataset)
    yield client

    # Tree format, the hub's canonical storage: the web UI's EntityService
    # persists serialize(format="tree"), and readers such as the version diff
    # view consume data["tree"]. A flat write here would make those readers see
    # an empty dataset. The spec hash is stamped on the same envelope the web
    # save path stamps, so an agent's write and a person's are indistinguishable
    # to the drift check.
    data = stamp_spec_hash(
        make_json_serializable(client.serialize(format="tree")),
        await dataset_spec_hash(session, dataset),
    )
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


def _create_one(
    client: Any, entity_type: str, data: dict[str, Any], parent_id: str | None
) -> dict[str, str]:
    """Create one entity through a loaded client, unvalidated.

    ``skip_validation``, because a partially filled entity is a normal
    intermediate state; what is missing is reported by the caller's validation
    report rather than refused, so an agent can build a dataset in steps.
    Shared by ``create_entity`` and ``batch_create`` so the two cannot drift.

    Args:
        client: A ``MetaseedClient`` over the dataset being edited.
        entity_type: A type from the dataset's profile.
        data: Field values, keyed by the field's codename.
        parent_id: The entity this one belongs under, or None for a root.

    Returns:
        The created entity's id and type.
    """
    entity = client.create_entity(entity_type, data, parent_id=parent_id, skip_validation=True)
    return {"id": entity.id, "entity_type": entity.entity_type}


def _batch_item(
    client: Any, index: int, item: dict[str, Any], created_ids: list[str | None]
) -> dict[str, Any]:
    """One batch_create item: resolve its parent, create it, report either way.

    A failed item is reported in place rather than raised, so one bad entity
    does not sink the rest of the batch — the same contract the standalone
    metaseed server's batch_create keeps.

    Args:
        client: A ``MetaseedClient`` over the dataset being edited.
        index: The item's position in the batch, echoed in the result.
        item: The item's entity_type, data, and parent_id or parent_index.
        created_ids: Ids of the batch's earlier items, None where one failed;
            what ``parent_index`` resolves against.

    Returns:
        A per-item result with ``status`` "created" or "error".
    """
    from metaseed import MetaseedError

    entity_type = item.get("entity_type")
    if not entity_type:
        return {"index": index, "status": "error", "message": "Missing entity_type"}

    parent_id = item.get("parent_id")
    parent_index = item.get("parent_index")
    if parent_index is not None:
        if not isinstance(parent_index, int) or not 0 <= parent_index < index:
            return {
                "index": index,
                "status": "error",
                "entity_type": entity_type,
                "message": (
                    f"parent_index {parent_index!r} must name an earlier item of this "
                    "batch; parents come before their children"
                ),
            }
        parent_id = created_ids[parent_index]
        if parent_id is None:
            return {
                "index": index,
                "status": "error",
                "entity_type": entity_type,
                "message": f"the item at parent_index {parent_index} was not created",
            }

    try:
        created = _create_one(client, entity_type, item.get("data") or {}, parent_id)
    except (MetaseedError, ValueError) as e:
        return {"index": index, "status": "error", "entity_type": entity_type, "message": str(e)}
    return {"index": index, "status": "created", **created}


async def _owned_draft(session: AsyncSession, user: User, name: str) -> SpecDraft:
    """The caller's own spec draft, by name.

    Scoped to the user, not only the tenant: draft names are unique per user
    (``uq_spec_drafts_tenant_user_name``), so a tenant-wide lookup could match
    several drafts and would let one user edit another's.
    """
    result = await session.execute(
        select(SpecDraft).where(
            SpecDraft.tenant_id == user.tenant_id,
            SpecDraft.user_id == user.id,
            SpecDraft.name == name,
        )
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


async def _published_spec(session: AsyncSession, profile: str, version: str) -> Spec | None:
    """The published specification a profile name and version refer to, if any.

    Publishing shares a specification with every user of the hub, so the lookup
    is deliberately not scoped to the caller's tenant. Matched case-insensitively
    because datasets store the lowercased profile name while list_profiles
    reports the name as published.
    """
    from sqlalchemy import func

    result = await session.execute(
        select(Spec).where(
            func.lower(Spec.name) == profile.lower(),
            Spec.version == version,
            Spec.status == SpecStatus.PUBLISHED,
            Spec.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def _profile_spec(session: AsyncSession, profile: str, version: str) -> Any:
    """The ``ProfileSpec`` behind a profile name: built-in first, then published.

    Raises:
        ValueError: If neither a built-in profile nor a published specification
            matches the name and version.
    """
    from metaseed.specs.loader import SpecLoader

    loader = SpecLoader()
    if profile.lower() in loader.list_profiles():
        versions = loader.list_versions(profile.lower())
        if version not in versions:
            raise ValueError(
                f"Profile {profile!r} has no version {version!r}; available: {versions}"
            )
        return loader.load_profile(version=version, profile=profile.lower())

    published = await _published_spec(session, profile, version)
    if published is None:
        raise ValueError(
            f"No profile named {profile!r} with version {version!r}. "
            "Call list_profiles for what exists."
        )
    from metaseed.specs.schema import ProfileSpec

    raw = published.spec_data or {}
    if isinstance(raw, dict) and "spec" in raw:
        raw = raw["spec"]
    try:
        return ProfileSpec(**raw)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"The published specification {published.name!r} {published.version!r} "
            f"could not be loaded: {e}"
        ) from e


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
            "create_entity and update_entity one entity at a time — or "
            "batch_create for several at once — rather than "
            "save_dataset, which replaces the whole dataset and overwrites "
            "anything changed meanwhile. Every edit reports what is still "
            "missing; work through that until validate_dataset is clean.\n\n"
            "Record only what the source states. Leave a field empty rather "
            "than inventing a value: an empty required field is a smaller "
            "problem than a wrong one, and it is reported so it can be filled "
            "later.\n\n"
            # The spec-building workflow (entity linkage, root, orphans) is
            # shared with the standalone metaseed server so both teach the
            # same tree model.
            + SPEC_BUILDING_INSTRUCTIONS
            + "\nA draft is correctable in place: spec_update_entity, "
            "spec_rename_entity, spec_delete_entity, spec_update_field, "
            "spec_delete_field, spec_move_field, the rule tools, and "
            "spec_set_metadata revise what exists, and spec_status "
            "summarizes where the draft stands. spec_delete_draft discards a "
            "whole draft, which nothing restores.\n"
            "\nDrafts are private to you. Publishing is deliberately not "
            "available here — it shares a specification with every user of "
            "the hub, so the person must do it themselves in the web "
            "interface."
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
        async with _caller() as (_session, user):
            return json.dumps({"email": user.email, "display_name": user.display_name})

    @mcp.tool()
    async def list_datasets() -> str:
        """List the datasets in the caller's own workspace."""
        async with _caller() as (session, user):
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

    @mcp.tool()
    async def get_dataset(name: str) -> str:
        """Return a dataset's stored contents.

        Args:
            name: The dataset's name in the caller's workspace.
        """
        async with _caller() as (session, user):
            dataset = await _owned_dataset(session, user, name)
            return json.dumps(
                {
                    "name": dataset.name,
                    "profile": dataset.profile,
                    "version": dataset.version,
                    "data": dataset.data,
                }
            )

    @mcp.tool()
    async def create_dataset(name: str, profile: str, version: str) -> str:
        """Create an empty dataset in the caller's workspace.

        The profile may be a built-in standard or a published specification;
        both appear in list_profiles.

        Args:
            name: A name unique within the workspace.
            profile: A profile name from list_profiles.
            version: The profile version.
        """
        from metaseed.specs.loader import SpecLoader

        async with _caller() as (session, user):
            # Checked without the deleted_at filter: the unique constraint
            # uq_datasets_tenant_name is not scoped to deleted_at, so a
            # soft-deleted row still holds the name and an insert would fail
            # with an IntegrityError the agent cannot act on.
            existing = await session.execute(
                select(Dataset).where(
                    Dataset.tenant_id == user.tenant_id,
                    Dataset.name == name,
                )
            )
            held = existing.scalar_one_or_none()
            if held is not None:
                if held.is_deleted:
                    raise ValueError(
                        f"The name {name!r} is held by a deleted dataset; choose a different name"
                    )
                raise ValueError(f"A dataset named {name!r} already exists")

            spec_id: str | None = None
            if profile.lower() in SpecLoader().list_profiles():
                # Validates the profile and version; loading is the check.
                await _profile_spec(session, profile, version)
                profile = profile.lower()
            else:
                published = await _published_spec(session, profile, version)
                if published is None:
                    raise ValueError(
                        f"No profile named {profile!r} with version {version!r}. "
                        "Call list_profiles for what exists."
                    )
                # Mirrors the web UI's dataset_create: the lowercased name plus
                # spec_id is what ensure_dataset_facade resolves the spec from.
                spec_id = published.id
                profile = published.name.lower()
                version = published.version

            dataset = Dataset(
                tenant_id=user.tenant_id,
                name=name,
                profile=profile,
                version=version,
                spec_id=spec_id,
                data={},
            )
            session.add(dataset)
            await session.commit()
            return json.dumps({"name": name, "profile": profile, "version": version})

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

        async with _caller() as (session, user):
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

    @mcp.tool()
    async def delete_dataset(name: str) -> str:
        """Remove a dataset from the caller's workspace.

        Soft: the dataset stops being listed but is not erased, so an agent
        cannot destroy someone's work irrecoverably.

        Args:
            name: The dataset to remove.
        """
        async with _caller() as (session, user):
            dataset = await _owned_dataset(session, user, name)
            dataset.soft_delete()
            await session.commit()
            logger.info("mcp: %s deleted dataset %r", user.email, name)
            return json.dumps({"name": name, "deleted": True})

    @mcp.tool()
    async def validate_dataset(name: str) -> str:
        """Check a dataset against its profile and report what is missing.

        Args:
            name: The dataset to validate, in the caller's workspace.
        """
        async with _caller() as (session, user):
            dataset = await _owned_dataset(session, user, name)
            report = await _validation_report(session, dataset)
            return json.dumps({"name": name, **report})

    @mcp.tool()
    async def list_profiles() -> str:
        """List the metadata standards available, built-in and published.

        Published specifications are included: publishing shares a specification
        with every user of the hub, so any of them can be used here. Every entry
        carries its versions, because get_profile_schema and create_dataset
        require an exact version.
        """
        from metaseed.specs.loader import SpecLoader

        loader = SpecLoader()
        built_in = [
            {"name": name, "versions": loader.list_versions(name), "source": "built_in"}
            for name in loader.list_profiles()
        ]

        async with _caller() as (session, _user):
            result = await session.execute(
                select(Spec).where(Spec.status == SpecStatus.PUBLISHED, Spec.deleted_at.is_(None))
            )
            published = [
                {"name": s.name, "versions": [s.version], "source": "published"}
                for s in result.scalars().all()
            ]
            return json.dumps({"built_in": built_in, "published": published})

    @mcp.tool()
    async def get_profile_schema(profile: str, version: str) -> str:
        """Return a profile's entity types and their fields.

        Call this before creating entities: only the types it names exist.
        Works for built-in profiles and published specifications alike.

        Args:
            profile: The profile name.
            version: The profile version.
        """
        async with _caller() as (session, _user):
            spec = await _profile_spec(session, profile, version)
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
        async with _caller() as (session, user):
            row = await _owned_dataset(session, user, dataset)
            async with _editing(session, row, user) as client:
                created = _create_one(client, entity_type, data, parent_id)
            report = await _validation_report(session, row)
            logger.info("mcp: %s created %s in %r", user.email, entity_type, dataset)
            return json.dumps({**created, **report})

    @mcp.tool()
    async def batch_create(dataset: str, entities: list[dict[str, Any]]) -> str:
        """Create several entities in one call, root-first.

        One call is one write: the previous dataset state is kept as a single
        version, however many entities the batch holds. Items are created in
        order, so a parent must come before its children; a failed item is
        reported in its result while the rest of the batch still lands.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entities: One object per entity, each with:
                entity_type: A type from get_profile_schema.
                data: Field values, keyed by the field's codename.
                parent_id: The id of an already stored entity to nest under.
                parent_index: The position (0-based) of an earlier item in
                    this batch to nest under, for parents this call creates.
        """
        if not entities:
            raise ValueError("The batch is empty; pass at least one entity")

        async with _caller() as (session, user):
            row = await _owned_dataset(session, user, dataset)
            results: list[dict[str, Any]] = []
            created_ids: list[str | None] = []
            async with _editing(session, row, user) as client:
                for index, item in enumerate(entities):
                    outcome = _batch_item(client, index, item, created_ids)
                    created_ids.append(outcome.get("id"))
                    results.append(outcome)
            report = await _validation_report(session, row)
            created = sum(1 for r in results if r["status"] == "created")
            logger.info(
                "mcp: %s batch-created %d/%d entities in %r",
                user.email,
                created,
                len(entities),
                dataset,
            )
            return json.dumps(
                {
                    "total": len(entities),
                    "created": created,
                    "failed": len(entities) - created,
                    "results": results,
                    **report,
                }
            )

    @mcp.tool()
    async def update_entity(dataset: str, entity_id: str, data: dict[str, Any]) -> str:
        """Change field values on one entity, leaving the rest of it alone.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_id: The entity to change, from list_entities.
            data: The fields to set. Fields not named keep their values.
        """
        async with _caller() as (session, user):
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

    @mcp.tool()
    async def delete_entity(dataset: str, entity_id: str) -> str:
        """Remove one entity from a dataset.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_id: The entity to remove.
        """
        async with _caller() as (session, user):
            row = await _owned_dataset(session, user, dataset)
            async with _editing(session, row, user) as client:
                client.delete_entity(entity_id)
            report = await _validation_report(session, row)
            logger.info("mcp: %s deleted entity %s in %r", user.email, entity_id, dataset)
            return json.dumps({"deleted": entity_id, **report})

    @mcp.tool()
    async def list_entities(dataset: str, entity_type: str | None = None) -> str:
        """List a dataset's entities, with their ids and labels.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_type: Restrict to one type. Omit for all of them.
        """
        from metaseed_hub.ui.helpers import make_json_serializable

        async with _caller() as (session, user):
            row = await _owned_dataset(session, user, dataset)
            # Read through the facade, not the raw JSONB: the hub stores the
            # tree format the web UI writes, and the facade loads that as well
            # as the legacy flat format.
            client = await _loaded_client(session, row)
            entities = [
                {
                    "id": e.get("_node_id"),
                    "entity_type": e.get("_type"),
                    "data": {k: v for k, v in e.items() if not k.startswith("_")},
                }
                for e in client.serialize()["entities"]
            ]
            if entity_type:
                entities = [e for e in entities if e["entity_type"] == entity_type]
            return json.dumps({"dataset": dataset, "entities": make_json_serializable(entities)})

    @mcp.tool()
    async def get_entity(dataset: str, entity_id: str) -> str:
        """Return one entity's stored field values.

        Args:
            dataset: The dataset's name in the caller's workspace.
            entity_id: The entity to read.
        """
        from metaseed import EntityNotFoundError

        from metaseed_hub.ui.helpers import make_json_serializable

        async with _caller() as (session, user):
            row = await _owned_dataset(session, user, dataset)
            client = await _loaded_client(session, row)
            try:
                entity = client.get_entity(entity_id)
            except EntityNotFoundError:
                raise ValueError(f"No entity {entity_id!r} in {dataset!r}") from None
            return json.dumps(
                {
                    "id": entity.id,
                    "entity_type": entity.entity_type,
                    "data": make_json_serializable(entity.data),
                }
            )

    register_spec_tools(
        mcp,
        caller=_caller,
        owned_draft=_owned_draft,
        building=_building,
        profile_spec=_profile_spec,
    )
    register_profile_tools(mcp, caller=_caller, profile_spec=_profile_spec)
    register_ontology_tools(mcp, caller=_caller)

    return mcp
