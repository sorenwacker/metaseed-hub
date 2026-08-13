"""The hub's rule tools must expose every attribute metaseed defines.

The same contract as the field markers, for the same reason: ``_rule_attributes``
refuses to run when metaseed knows an attribute the tool does not, rather than
silently dropping it — so an attribute added to metaseed breaks these tools the
moment the hub upgrades, and only at runtime. This moves that failure into the
hub's own CI, where the fix is to add the parameter.

Both tools took six arguments until metaseed 0.34.0, so a caller could declare a
cardinality rule's type but not its bounds.
"""

from __future__ import annotations

import inspect

import pytest
from metaseed.specs.builder import RULE_ATTRIBUTE_NAMES

from tests.mcp_helpers import _tool


@pytest.mark.parametrize("tool_name", ["spec_add_rule", "spec_update_rule"])
async def test_the_tool_exposes_every_rule_attribute(server, tool_name: str) -> None:
    fn = await _tool(server, tool_name)
    parameters = set(inspect.signature(fn).parameters)
    missing = sorted(set(RULE_ATTRIBUTE_NAMES) - parameters)
    assert not missing, (
        f"{tool_name} does not accept {missing}, which metaseed defines. The tool "
        "raises at runtime rather than dropping the attribute, so add the parameter."
    )
