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

from tests.mcp_helpers import _calling_with, _drafting, _field, _spec_of, _tool


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


@pytest.mark.parametrize("tool_name", ["spec_add_field", "spec_update_field"])
def test_the_tool_offers_every_constraint_metaseed_defines(tool_name: str) -> None:
    """Hardcoding the names here would let the hub and metaseed drift apart.
    Both tools must expose every constraint: add builds a field, update edits
    one, and a constraint missing from either is unsettable."""
    import inspect

    from metaseed.specs.builder import CONSTRAINT_NAMES

    from metaseed_hub.mcp import create_mcp_server

    server = create_mcp_server()
    signature = inspect.signature(server._tool_manager.get_tool(tool_name).fn)
    for name in CONSTRAINT_NAMES:
        assert name in signature.parameters, f"{tool_name} cannot set {name}"
    if tool_name == "spec_update_field":
        assert "clear" in signature.parameters


def test_the_add_field_constraint_builder_shares_the_completeness_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec_add_field built constraints through _constraints, which did not
    enforce the completeness guard _constraint_values does, so a new metaseed
    constraint was silently unaddable rather than reported."""
    import metaseed.specs.builder as builder_mod

    from metaseed_hub.mcp._spec_tools import _constraints

    monkeypatch.setattr(
        builder_mod, "CONSTRAINT_NAMES", (*builder_mod.CONSTRAINT_NAMES, "future_constraint")
    )
    with pytest.raises(ValueError, match="future_constraint"):
        _constraints({"pattern": "x"})
