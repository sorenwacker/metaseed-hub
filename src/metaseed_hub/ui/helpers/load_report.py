"""Reporting the stored nodes that a permissive load could not place.

The hub loads dataset payloads permissively (``client.load(..., on_skip=...)``):
a node the profile cannot place -- not a mapping, no ``entity_type``, an entity
type the schema does not define, or one whose creation fails -- is dropped with
its subtree instead of failing the whole load. Without that tolerance one bad
node makes a whole dataset unreadable.

The tolerance has a cost that must not stay hidden. A dropped node is absent
from the loaded facade, every save serializes that facade, so the next save
deletes from storage what did not load. A dataset that quietly shrinks is worse
than one that refuses to open, so each drop is turned into a validation issue
and reported through the paths that already report dataset problems: the MCP
validation report and the web validation panel.

Reporting is enough for someone who asked. It is not enough for an agent that
did not: it edits an unrelated entity, the save rewrites the payload from what
loaded, and the tool reports success. So the same facts are also shaped into a
refusal (``unloadable_node_refusal``), which the MCP ``_editing`` context raises
instead of letting a mutation through.

See `docs/developer/architecture.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from metaseed import SkippedNode

SKIPPED_NODE_RULE = "unloadable_node"
"""The ``rule`` a reported skip carries, alongside metaseed's own rule names."""

SKIPPED_NODE_NEXT_STEP = (
    f" The {SKIPPED_NODE_RULE} item(s) are not missing values: those nodes are in "
    "storage but are not in the dataset as loaded, so nothing can edit them and "
    "the next save removes them. Restore an earlier version, or correct the "
    "specification, before saving over them."
)
"""Appended to an agent's next step, because these issues cannot be 'filled in'."""


def unloadable_node_refusal(skipped: Sequence[SkippedNode]) -> str:
    """Explain why an edit is refused, and what the caller can do instead.

    Every edit rewrites the whole stored payload from the loaded facade, so an
    edit to an unrelated entity is what deletes the nodes that did not load.
    The refusal has to carry enough for a caller to decide: how much is at
    stake, where it still exists, and which call does the drop deliberately if
    that is what was wanted.

    Args:
        skipped: The nodes the load dropped. Must not be empty; the caller
            refuses only when there is something to refuse over.

    Returns:
        The refusal message, naming the count, the entity types, and
        ``save_dataset`` as the deliberate way through.
    """
    types = sorted({skip.entity_type or "untyped" for skip in skipped})
    return (
        f"{len(skipped)} stored node(s) did not load ({', '.join(types)}), so this "
        "dataset cannot be edited. Those nodes are in storage but are not part of "
        "the dataset as loaded, and every edit rewrites the dataset from what "
        "loaded -- so this edit would delete them. Restore an earlier version, or "
        "correct the specification so they load, and the edit will go through. "
        "If you do mean to drop them, save_dataset replaces the whole dataset and "
        "removes them deliberately. validate_dataset lists them one by one."
    )


def skipped_node_message(skip: SkippedNode) -> str:
    """Describe one dropped node, and what dropping it costs.

    Args:
        skip: The node metaseed's loader could not place.

    Returns:
        A sentence for a validation report, naming the node, why it did not
        load, and how much was dropped with it.
    """
    described = f"{skip.entity_type} node" if skip.entity_type else "A stored node"
    node_id = _node_id(skip)
    identified = f" {node_id!r}" if node_id else ""
    lost = (
        f" {skip.descendants_dropped} node(s) below it were dropped with it."
        if skip.descendants_dropped
        else ""
    )
    return (
        f"{described}{identified} did not load: {skip.reason}. It is not part of "
        f"the dataset as loaded, so saving removes it from storage.{lost}"
    )


def skipped_node_issues(skipped: Iterable[SkippedNode]) -> list[dict[str, Any]]:
    """Turn dropped nodes into validation issues, in the shape a report uses.

    Args:
        skipped: The nodes a load dropped, in the order they were dropped.

    Returns:
        One issue record per dropped node. ``entity_id`` is the node's stored
        id where it has one -- it identifies the node in the stored payload,
        which is the only place the node still exists.
    """
    return [
        {
            "entity_id": _node_id(skip),
            "field": None,
            "rule": SKIPPED_NODE_RULE,
            "message": skipped_node_message(skip),
        }
        for skip in skipped
    ]


def _node_id(skip: SkippedNode) -> str | None:
    """The dropped node's stored id, if it has a usable one.

    Args:
        skip: The node metaseed's loader could not place.

    Returns:
        The node's ``id``, or None -- a node can be dropped precisely because it
        is not a mapping, and one that carried no id has nothing to name it by.
    """
    if not isinstance(skip.node, dict):
        return None
    node_id = skip.node.get("id")
    return node_id if isinstance(node_id, str) and node_id else None
