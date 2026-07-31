"""Editing one constraint over MCP must not discard the others.

``spec_update_field`` used to accept no constraints at all, so an agent that
wanted to tighten a bound had to resend the whole field through
``update_field(constraints=...)`` -- a whole-object replacement that silently
dropped every constraint it did not repeat. These tests pin the merging
behaviour metaseed's ``SpecBuilder.update_field_constraints`` provides, driven
through the hub's tool so the wiring is what is exercised.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_mcp_endpoint import _calling_with, _tool

# Imported under an alias and re-exposed by assignment: pytest discovers the
# fixture by module attribute name, and a plain import would trip F811 on every
# test whose `server` parameter shadows it.
from tests.test_mcp_endpoint import server as mcp_server
from tests.test_mcp_spec_tools import _drafting, _field, _spec_of

server = mcp_server


async def _constrained_field(server, session: AsyncSession, name: str) -> str:
    """A draft holding ``Sample.count`` with a minimum and a maximum.

    Returns:
        The token secret of the user owning the draft.
    """
    secret = await _drafting(server, session, slug=name.lower(), name=name)
    add_entity = await _tool(server, "spec_add_entity")
    add_field = await _tool(server, "spec_add_field")
    with _calling_with(secret):
        await add_entity(name, "Sample", "a sample")
        await add_field(
            name,
            "Sample",
            "count",
            "integer",
            minimum=1,
            maximum=100,
        )
    return secret


async def test_updating_one_constraint_keeps_the_others(server, session) -> None:
    """Tightening the maximum must leave the minimum in place."""
    secret = await _constrained_field(server, session, "ConstraintKeep")
    update = await _tool(server, "spec_update_field")

    with _calling_with(secret):
        await update("ConstraintKeep", "Sample", "count", maximum=50)

    constraints = _field(await _spec_of("ConstraintKeep"), "Sample", "count")["constraints"]
    assert constraints["maximum"] == 50
    assert constraints["minimum"] == 1, "the untouched minimum was discarded"


async def test_clear_removes_only_what_it_names(server, session) -> None:
    """``clear`` is how removal is expressed; it must not take the rest with it."""
    secret = await _constrained_field(server, session, "ConstraintClear")
    update = await _tool(server, "spec_update_field")

    with _calling_with(secret):
        await update("ConstraintClear", "Sample", "count", clear=["minimum"])

    constraints = _field(await _spec_of("ConstraintClear"), "Sample", "count")["constraints"]
    assert "minimum" not in constraints
    assert constraints["maximum"] == 100, "clearing the minimum removed the maximum too"


async def test_setting_and_clearing_the_same_constraint_is_refused(server, session) -> None:
    """The two requests contradict each other, so neither is guessed at."""
    secret = await _constrained_field(server, session, "ConstraintConflict")
    update = await _tool(server, "spec_update_field")

    with _calling_with(secret), pytest.raises(ValueError, match="minimum"):
        await update("ConstraintConflict", "Sample", "count", minimum=5, clear=["minimum"])

    constraints = _field(await _spec_of("ConstraintConflict"), "Sample", "count")["constraints"]
    assert constraints["minimum"] == 1, "the refused call still changed the field"


async def test_an_unknown_constraint_name_in_clear_is_refused(server, session) -> None:
    """The valid names come from metaseed, so the hub cannot drift from them."""
    secret = await _constrained_field(server, session, "ConstraintUnknown")
    update = await _tool(server, "spec_update_field")

    with _calling_with(secret), pytest.raises(ValueError, match="not_a_constraint"):
        await update("ConstraintUnknown", "Sample", "count", clear=["not_a_constraint"])


async def test_non_constraint_attributes_still_update(server, session) -> None:
    """The constraint path is additive: the ordinary attributes still change."""
    secret = await _constrained_field(server, session, "ConstraintBoth")
    update = await _tool(server, "spec_update_field")

    with _calling_with(secret):
        await update("ConstraintBoth", "Sample", "count", required=True, minimum=2)

    field = _field(await _spec_of("ConstraintBoth"), "Sample", "count")
    assert field["required"] is True
    assert field["constraints"]["minimum"] == 2
    assert field["constraints"]["maximum"] == 100


def test_the_tool_offers_every_constraint_metaseed_defines() -> None:
    """Hardcoding the names here would let the hub and metaseed drift apart."""
    import inspect

    from metaseed.specs.builder import CONSTRAINT_NAMES

    from metaseed_hub.mcp import create_mcp_server

    server = create_mcp_server()
    signature = inspect.signature(server._tool_manager.get_tool("spec_update_field").fn)
    for name in CONSTRAINT_NAMES:
        assert name in signature.parameters, f"spec_update_field cannot set {name}"
    assert "clear" in signature.parameters
