"""Profile relationship tools for the hub's MCP endpoint.

Reports the parent-child hierarchy of a profile, for built-in standards and
published specifications alike, resolved through the same ``_profile_spec``
helper the schema tool uses. The output shape mirrors the standalone metaseed
server's ``get_profile_relationships``, so an agent moving between the two
reads the same map.

The registrar takes the shared helpers as arguments rather than importing them
from the package, so the package can import this module without a cycle.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import User

    Caller = Callable[[], AbstractAsyncContextManager[tuple[AsyncSession, User]]]
    ProfileSpecLookup = Callable[[AsyncSession, str, str], Awaitable[Any]]


def _identifier_info(entity_def: Any) -> tuple[str | None, str | None]:
    """An entity's identifier field, and a note where it breaks convention.

    The identifier is the field marked ``is_identifier``, or failing that the
    first non-reference field — the same convention metaseed's EntityHelper
    applies at runtime. The note flags entities without a ``unique_id`` field,
    so an agent stops assuming ``unique_id`` everywhere.

    Args:
        entity_def: An entity definition spec with a ``fields`` list.

    Returns:
        A ``(identifier, note)`` pair; note is None for conventional entities.
    """
    field_names = {f.name for f in entity_def.fields}
    identifier = next(
        (f.name for f in entity_def.fields if f.is_identifier),
        next((f.name for f in entity_def.fields if not f.reference), None),
    )
    note = None
    if identifier and identifier != "unique_id" and "unique_id" not in field_names:
        note = (
            f"Unlike most entities, this type has no 'unique_id' field; "
            f"its identifier is {identifier!r}."
        )
    return identifier, note


def register_profile_tools(
    mcp: FastMCP, *, caller: Caller, profile_spec: ProfileSpecLookup
) -> None:
    """Register the profile relationship tools with the hub's MCP server.

    Args:
        mcp: The FastMCP server to add the tools to.
        caller: Async context manager resolving the current call's token to a
            ``(session, user)`` pair.
        profile_spec: Coroutine resolving a profile name and version to a
            ``ProfileSpec``, built-in first, then published.
    """

    @mcp.tool()
    async def get_profile_relationships(profile: str, version: str) -> str:
        """Return a profile's entity hierarchy: parents, children, references.

        Shows, for every entity type, its identifier field, the child entity
        types it can contain, and its cross-reference fields (which entity and
        field each reference points at). Call this before creating entities so
        the dataset is built relationally instead of flat. Works for built-in
        profiles and published specifications alike.

        Args:
            profile: The profile name.
            version: The profile version.
        """
        from metaseed.facade import ProfileFacade

        async with caller() as (session, _user):
            spec = await profile_spec(session, profile, version)

        facade = ProfileFacade(spec.name, spec=spec)
        hierarchy: dict[str, Any] = {}
        for entity_name, entity_def in spec.entities.items():
            helper = getattr(facade, entity_name, None)
            children = sorted(set(helper.nested_fields.values())) if helper else []
            cross_references = (
                {
                    field: f"{target_type}.{target_field}"
                    for field, (target_type, target_field) in helper.reference_fields.items()
                }
                if helper
                else {}
            )
            identifier, note = _identifier_info(entity_def)
            info: dict[str, Any] = {
                "identifier": identifier,
                "children": children,
                "cross_references": cross_references,
            }
            if note:
                info["note"] = note
            hierarchy[entity_name] = info

        return json.dumps(
            {
                "profile": spec.name,
                "version": spec.version,
                "root_entity": spec.root_entity,
                "hierarchy": hierarchy,
            }
        )
