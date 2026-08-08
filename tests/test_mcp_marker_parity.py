"""The hub's field tools must expose every marker metaseed defines.

``_markers`` refuses to run when metaseed knows a marker the tool does not,
rather than silently dropping it. That is right, but it means a marker added to
metaseed breaks these tools the moment the hub upgrades, and only at runtime.
This test moves that failure into the hub's own CI, where the fix is to add the
parameter.
"""

from __future__ import annotations

import inspect

import pytest
from metaseed.specs.builder import FIELD_MARKER_NAMES

from tests.mcp_helpers import _tool


@pytest.mark.parametrize("tool_name", ["spec_add_field", "spec_update_field"])
async def test_the_tool_exposes_every_marker_metaseed_defines(server, tool_name: str) -> None:
    fn = await _tool(server, tool_name)
    parameters = set(inspect.signature(fn).parameters)
    missing = sorted(set(FIELD_MARKER_NAMES) - parameters)
    assert not missing, (
        f"{tool_name} does not accept {missing}, which metaseed defines. The tool "
        "raises at runtime rather than dropping the marker, so add the parameter."
    )
