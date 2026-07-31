"""The endpoint's instructions, checked against the tools that exist.

The instructions are the agent's whole picture of the endpoint: a tool they do
not mention is a tool no agent will use, and an argument they show that no tool
accepts is an error every agent that follows them will hit.
"""

from __future__ import annotations

import inspect
import re


async def test_the_instructions_cover_the_tools_that_exist(server) -> None:
    """The instructions described only the original nine tools after entity and
    spec tools were added, so an agent had no reason to think it could edit
    incrementally — and would keep replacing whole datasets."""
    instructions = server.instructions or ""

    for expected in ("create_entity", "update_entity", "spec_create", "spec_validate"):
        assert expected in instructions, f"{expected} is not mentioned"
    assert "save_dataset, which replaces the whole dataset" in instructions
    assert "Publishing is deliberately not available" in instructions


async def test_the_instructions_teach_spec_entity_linkage(server) -> None:
    """Without the linkage workflow an agent builds a flat spec: entities but
    no parent field whose items names the child, no root — and spec_validate
    does not flag orphans. The shared block from metaseed carries it so the
    hub and the standalone server teach the same tree model."""
    from metaseed.agent.mcp.server import SPEC_BUILDING_INSTRUCTIONS

    instructions = server.instructions or ""
    assert SPEC_BUILDING_INSTRUCTIONS in instructions
    for expected in ("spec_set_root_entity", "items", "orphan"):
        assert expected in instructions, f"{expected} is not mentioned"


_TOOL_MENTION = re.compile(r"\b(spec_[a-z_]+)\b")
_PARAMETER_MENTION = re.compile(r"\b([a-z_]+)=")


def _sentences(text: str) -> list[str]:
    """Split instructions into sentences, so a tool and its arguments pair up."""
    return [part for part in re.split(r"(?<=\.)\s|\n", text) if part.strip()]


async def test_the_instructions_only_name_tools_and_arguments_that_exist(server) -> None:
    """The instructions are the agent's whole picture of the endpoint.

    They told agents to call spec_add_field with items=<ChildEntityName> while
    the tool had no items parameter, so every agent that followed them hit an
    unexpected-argument error. Checked generally: any spec_* tool named, and any
    ``argument=`` shown, must exist in the registered signatures.
    """
    instructions = server.instructions or ""
    registered = {t.name for t in await server.list_tools()}
    parameters = {
        name: set(inspect.signature(server._tool_manager.get_tool(name).fn).parameters)
        for name in registered
    }
    known_parameters = set().union(*parameters.values())

    for mentioned in sorted(set(_TOOL_MENTION.findall(instructions))):
        assert mentioned in registered, f"the instructions name {mentioned}, which is not a tool"

    for sentence in _sentences(instructions):
        named = sorted(set(_TOOL_MENTION.findall(sentence)) & registered)
        for argument in sorted(set(_PARAMETER_MENTION.findall(sentence))):
            # One tool in the sentence means the argument is that tool's;
            # otherwise it only has to belong to some registered tool.
            expected = parameters[named[0]] if len(named) == 1 else known_parameters
            where = named[0] if len(named) == 1 else "any tool"
            assert argument in expected, (
                f"the instructions show {argument}= but {where} has no such parameter"
            )


async def test_the_instructions_mention_correctability(server) -> None:
    """An agent that does not know a draft is correctable rebuilds it."""
    instructions = server.instructions or ""
    for expected in ("spec_update_field", "spec_rename_entity", "spec_status"):
        assert expected in instructions, f"{expected} is not mentioned"
