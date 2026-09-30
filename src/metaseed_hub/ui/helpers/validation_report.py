"""One source of validation issues for every report the hub renders.

The web panel and the MCP report each asked their own question: the panel
re-created every entity with Pydantic and reported what that raised -- a
wrong type, and nothing else -- while the MCP path asked metaseed's validator,
so an agent and a person disagreed about the same dataset. Both take their
issues from here now (docs/developer/architecture.md, "Validation").
"""

from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool


async def validation_issues(state: Any) -> list[dict[str, Any]]:
    """Every issue metaseed's validator reports for the loaded dataset.

    Runs ``MetaseedClient.validate()`` over the facade in a worker thread: it is
    pure computation, seconds at thousands of entities, and on the request loop
    it would hold every other request for that long. The issues are passed
    through as metaseed reports them -- they are derived from the
    specification, so re-deriving any of it here would only let the two
    disagree; ``rule`` names the specification rule that failed.

    Args:
        state: A loaded AppState whose facade holds the dataset.

    Returns:
        One record per issue: ``entity_id`` (the node it belongs to, or None
        for a finding about the dataset as a whole), ``field``, ``rule`` and
        ``message``.
    """
    from metaseed import MetaseedClient

    client = MetaseedClient.from_facade(state.facade)
    result = await run_in_threadpool(client.validate)
    return [
        {"entity_id": i.entity_id, "field": i.field, "rule": i.rule, "message": i.message}
        for i in result.issues
    ]
