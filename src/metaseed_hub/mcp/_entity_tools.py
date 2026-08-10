"""Entity tools for the hub's MCP endpoint.

The tools that read and change the entities inside one dataset, as opposed to
the dataset-level tools that create, replace or delete the dataset itself. Every
mutation runs inside the ``editing`` context, which loads the dataset, refuses
when a stored node did not load, and writes the result back with a version
snapshot -- so no tool here repeats that reasoning.

The registrar takes the shared helpers as arguments rather than importing them
from the package, so the package can import this module without a cycle.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import Dataset, User

    Caller = Callable[[], AbstractAsyncContextManager[tuple[AsyncSession, User]]]
    OwnedDataset = Callable[[AsyncSession, User, str], Awaitable[Dataset]]
    Editing = Callable[[AsyncSession, Dataset, User], AbstractAsyncContextManager[Any]]
    LoadedClient = Callable[[AsyncSession, Dataset], Awaitable[Any]]
    ValidationReport = Callable[[AsyncSession, Dataset], Awaitable[dict[str, Any]]]

logger = logging.getLogger("metaseed_hub")


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


def register_entity_tools(
    mcp: FastMCP,
    *,
    caller: Caller,
    owned_dataset: OwnedDataset,
    editing: Editing,
    loaded_client: LoadedClient,
    validation_report: ValidationReport,
) -> None:
    """Register the entity tools with the hub's MCP server.

    Args:
        mcp: The FastMCP server to add the tools to.
        caller: Async context manager resolving the current call's token to a
            ``(session, user)`` pair.
        owned_dataset: Coroutine returning a dataset from the caller's own
            account by name.
        editing: Async context manager yielding a client over a dataset and
            persisting the result, with the snapshot and the refusal.
        loaded_client: Coroutine returning a read-only client over a dataset.
        validation_report: Coroutine reporting what a dataset is still missing.
    """

    @mcp.tool()
    async def create_entity(
        dataset: str,
        entity_type: str,
        data: dict[str, Any],
        parent_id: str | None = None,
    ) -> str:
        """Add one entity to a dataset, without rewriting the rest.

        Args:
            dataset: The dataset's name in the caller's account.
            entity_type: A type from get_profile_schema. Any other is rejected.
            data: Field values, keyed by the field's codename.
            parent_id: The entity this one belongs under, where the profile
                nests them. Omit for a root entity.
        """
        async with caller() as (session, user):
            row = await owned_dataset(session, user, dataset)
            async with editing(session, row, user) as client:
                created = _create_one(client, entity_type, data, parent_id)
            report = await validation_report(session, row)
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
            dataset: The dataset's name in the caller's account.
            entities: One object per entity, each with:
                entity_type: A type from get_profile_schema.
                data: Field values, keyed by the field's codename.
                parent_id: The id of an already stored entity to nest under.
                parent_index: The position (0-based) of an earlier item in
                    this batch to nest under, for parents this call creates.
        """
        if not entities:
            raise ValueError("The batch is empty; pass at least one entity")

        async with caller() as (session, user):
            row = await owned_dataset(session, user, dataset)
            results: list[dict[str, Any]] = []
            created_ids: list[str | None] = []
            async with editing(session, row, user) as client:
                for index, item in enumerate(entities):
                    outcome = _batch_item(client, index, item, created_ids)
                    created_ids.append(outcome.get("id"))
                    results.append(outcome)
            report = await validation_report(session, row)
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
            dataset: The dataset's name in the caller's account.
            entity_id: The entity to change, from list_entities.
            data: The fields to set. Fields not named keep their values.
        """
        async with caller() as (session, user):
            row = await owned_dataset(session, user, dataset)
            async with editing(session, row, user) as client:
                # Merged, not replaced. metaseed's update_entity overwrites the
                # entity's data wholesale, so passing one field would silently
                # drop every other value -- and an agent setting one field at a
                # time would destroy its own earlier work.
                merged = {**client.get_entity(entity_id).data, **data}
                entity = client.update_entity(entity_id, merged, skip_validation=True)
                updated = {"id": entity.id, "entity_type": entity.entity_type}
            report = await validation_report(session, row)
            logger.info("mcp: %s updated %s in %r", user.email, entity_id, dataset)
            return json.dumps({**updated, **report})

    @mcp.tool()
    async def delete_entity(dataset: str, entity_id: str) -> str:
        """Remove one entity from a dataset.

        Args:
            dataset: The dataset's name in the caller's account.
            entity_id: The entity to remove.
        """
        async with caller() as (session, user):
            row = await owned_dataset(session, user, dataset)
            async with editing(session, row, user) as client:
                client.delete_entity(entity_id)
            report = await validation_report(session, row)
            logger.info("mcp: %s deleted entity %s in %r", user.email, entity_id, dataset)
            return json.dumps({"deleted": entity_id, **report})

    @mcp.tool()
    async def list_entities(dataset: str, entity_type: str | None = None) -> str:
        """List a dataset's entities, with their ids and labels.

        Args:
            dataset: The dataset's name in the caller's account.
            entity_type: Restrict to one type. Omit for all of them.
        """
        from metaseed_hub.ui.helpers import make_json_serializable

        async with caller() as (session, user):
            row = await owned_dataset(session, user, dataset)
            # Read through the facade, not the raw JSONB: the hub stores the
            # tree format the web UI writes, and the facade loads that as well
            # as the legacy flat format.
            client = await loaded_client(session, row)
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
            dataset: The dataset's name in the caller's account.
            entity_id: The entity to read.
        """
        from metaseed import EntityNotFoundError

        from metaseed_hub.ui.helpers import make_json_serializable

        async with caller() as (session, user):
            row = await owned_dataset(session, user, dataset)
            client = await loaded_client(session, row)
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
