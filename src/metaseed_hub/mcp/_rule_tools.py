"""Validation-rule tools for the hub's MCP endpoint.

Split out of ``_spec_tools`` when exposing every attribute of metaseed's rule
format took that module past the thousand-line limit. The seam is by subject:
these three tools are the whole of rule authoring, and they share one guard —
:func:`_rule_attributes`, which refuses a call rather than silently dropping an
attribute metaseed defines and this tool does not.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from metaseed_hub.mcp._spec_tools import _clean

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import SpecDraft, User

    Caller = Callable[[], AbstractAsyncContextManager[tuple[AsyncSession, User]]]
    OwnedDraft = Callable[[AsyncSession, User, str], Awaitable[SpecDraft]]
    Building = Callable[[AsyncSession, SpecDraft, User], AbstractAsyncContextManager[Any]]


def _rule_attributes(arguments: dict[str, Any]) -> dict[str, Any]:
    """The rule attributes among a tool's arguments.

    Read off the tool's own locals against metaseed's
    :data:`~metaseed.specs.builder.RULE_ATTRIBUTE_NAMES`, the same contract the
    field markers follow: an attribute metaseed defines that this tool does not
    expose would be silently unauthorable over MCP, so the call is refused
    before it mutates anything and the fix is to add the parameter.

    Raises:
        ValueError: If metaseed defines a rule attribute this tool does not
            expose.
    """
    from metaseed.specs.builder import RULE_ATTRIBUTE_NAMES

    missing = sorted(set(RULE_ATTRIBUTE_NAMES) - set(arguments))
    if missing:
        raise ValueError(
            f"This tool does not expose the rule attribute(s) {', '.join(missing)}, "
            "which metaseed defines. Edit the rule through the web interface "
            "until the tool is extended."
        )
    return {name: arguments[name] for name in RULE_ATTRIBUTE_NAMES}


def register_rule_tools(
    mcp: FastMCP,
    *,
    caller: Caller,
    owned_draft: OwnedDraft,
    building: Building,
) -> None:
    """Register the validation-rule tools with the hub's MCP server.

    Args:
        mcp: The FastMCP server to add the tools to.
        caller: Async context manager resolving the current call's token to a
            ``(session, user)`` pair.
        owned_draft: Coroutine returning the caller's own draft by name.
        building: Async context manager yielding a ``SpecBuilder`` over a draft
            and persisting the draft after the block.
    """

    @mcp.tool()
    async def spec_add_rule(
        draft: str,
        name: str,
        type: str | None = None,
        description: str | None = None,
        message: str | None = None,
        applies_to: str | list[str] | None = None,
        field: str | None = None,
        condition: str | None = None,
        pattern: str | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        enum: list[str] | None = None,
        reference: str | None = None,
        unique_within: str | None = None,
        min_items: int | None = None,
        max_items: int | None = None,
        lat_field: str | None = None,
        lon_field: str | None = None,
        start_field: str | None = None,
        end_field: str | None = None,
        where: dict[str, Any] | None = None,
        when: dict[str, Any] | None = None,
        require: list[str] | None = None,
    ) -> str:
        """Add a validation rule to a draft specification.

        Args:
            draft: The draft's name in the caller's account.
            name: The rule's name.
            type: The rule type: conditional, date_range, coordinate_pair,
                cardinality, uniqueness or reference.
            description: What the rule checks.
            message: What a dataset is told when the rule fails.
            applies_to: The entity type(s) the rule checks — one name or a list.
            field: The field the rule checks, where one applies.
            condition: A boolean expression over field names, for a
                conditional rule. Tests presence only; use when/require to
                depend on a value.
            pattern: A regular expression the field must match.
            minimum: The smallest a numeric value may be.
            maximum: The largest a numeric value may be.
            enum: The values allowed.
            reference: The reference the rule checks, where one applies.
            unique_within: The scope a uniqueness rule holds in: parent or
                global.
            min_items: The fewest items a cardinality rule allows.
            max_items: The most items a cardinality rule allows.
            lat_field: The latitude field of a coordinate pair.
            lon_field: The longitude field of a coordinate pair.
            start_field: The start field of a date range.
            end_field: The end field of a date range.
            where: A predicate selecting which items a cardinality rule counts,
                or which records a uniqueness rule compares, as a mapping:
                {"field": "is_display_column", "op": "==", "value": true}, or a
                group {"all": [...]} / {"any": [...]} / {"not": {...}}.
            when: A predicate deciding whether ``require`` applies to a record.
            require: The fields a record must carry when ``when`` holds.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.add_rule(name, **_clean(_rule_attributes(locals())))
                problems = builder.validate()
            return json.dumps({"rule": name, "problems": problems})

    @mcp.tool()
    async def spec_update_rule(
        draft: str,
        rule_name: str,
        type: str | None = None,
        description: str | None = None,
        message: str | None = None,
        applies_to: str | list[str] | None = None,
        field: str | None = None,
        condition: str | None = None,
        pattern: str | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        enum: list[str] | None = None,
        reference: str | None = None,
        unique_within: str | None = None,
        min_items: int | None = None,
        max_items: int | None = None,
        lat_field: str | None = None,
        lon_field: str | None = None,
        start_field: str | None = None,
        end_field: str | None = None,
        where: dict[str, Any] | None = None,
        when: dict[str, Any] | None = None,
        require: list[str] | None = None,
    ) -> str:
        """Change a validation rule in place. Unset arguments keep their values.

        See ``spec_add_rule`` for what each attribute means.

        Args:
            draft: The draft's name in the caller's account.
            rule_name: The rule to change.
            type: The rule type.
            description: What the rule checks.
            message: What a dataset is told when the rule fails.
            applies_to: The entity type(s) the rule checks — one name or a list.
            field: The field the rule checks, where one applies.
            condition: A boolean expression over field names.
            pattern: A regular expression the field must match.
            minimum: The smallest a numeric value may be.
            maximum: The largest a numeric value may be.
            enum: The values allowed.
            reference: The reference the rule checks, where one applies.
            unique_within: The scope a uniqueness rule holds in.
            min_items: The fewest items a cardinality rule allows.
            max_items: The most items a cardinality rule allows.
            lat_field: The latitude field of a coordinate pair.
            lon_field: The longitude field of a coordinate pair.
            start_field: The start field of a date range.
            end_field: The end field of a date range.
            where: The predicate selecting which records the rule applies to.
            when: The predicate deciding whether ``require`` applies.
            require: The fields a record must carry when ``when`` holds.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.update_rule(rule_name, **_clean(_rule_attributes(locals())))
                problems = builder.validate()
            return json.dumps({"rule": rule_name, "problems": problems})

    @mcp.tool()
    async def spec_delete_rule(draft: str, rule_name: str) -> str:
        """Remove a validation rule from a draft specification.

        Args:
            draft: The draft's name in the caller's account.
            rule_name: The rule to remove.
        """
        async with caller() as (session, user):
            row = await owned_draft(session, user, draft)
            async with building(session, row, user) as builder:
                builder.delete_rule(rule_name)
                problems = builder.validate()
            return json.dumps({"deleted": rule_name, "problems": problems})
