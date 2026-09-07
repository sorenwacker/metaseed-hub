"""Focusing one entity must draw it connected, and at a sane depth.

The graph page's ``?node_id=`` view was built by following every edge outward
from the focused node. Real graph data broke that in three ways at once, none of
which the existing synthetic fixture could show, because it had no reference
edges and no levels:

- an entity's *reference* edges (dashed, e.g. ``Sample.study_ref``) point at
  things that are not its children, so the walk escaped upward and sideways and
  returned the entire graph — focusing narrowed nothing;
- a leaf's only edge is the one coming *into* it from its parent, so focusing a
  leaf — an ENA ``SampleAttribute``, drawn with its ``tag`` as the label —
  returned one node and no edges: a tag floating unconnected on the canvas;
- kept nodes carried the level they had in the whole graph, so a view rooted at
  a level-2 node was laid out hanging from two ranks of nothing.

The view is the focused entity in context: the containment path down to it, and
everything it contains.
"""

from __future__ import annotations

from typing import Any

from metaseed_hub.ui.routes.dataset.editor import _filter_graph_to_subtree

# A graph shaped like the real one: containment edges are solid, references are
# dashed, and every node carries the level it sits at in the whole graph.
#
#   study ── sample ── attribute        (containment)
#     │        └────── run
#     └── other_sample
#   sample ┄┄> study, run ┄┄> sample    (references, pointing back up)
GRAPH: dict[str, Any] = {
    "nodes": [
        {"id": "study", "group": "Study", "level": 0},
        {"id": "sample", "group": "Sample", "level": 1},
        {"id": "attribute", "group": "SampleAttribute", "level": 2},
        {"id": "run", "group": "Run", "level": 2},
        {"id": "other_sample", "group": "Sample", "level": 1},
    ],
    "edges": [
        {"from": "study", "to": "sample"},
        {"from": "sample", "to": "attribute"},
        {"from": "sample", "to": "run"},
        {"from": "study", "to": "other_sample"},
        {"from": "sample", "to": "study", "dashes": True, "label": "study_ref"},
        {"from": "run", "to": "sample", "dashes": True, "label": "sample_ref"},
    ],
    "entity_types": ["Study", "Sample", "SampleAttribute", "Run"],
}


def _ids(graph: dict[str, Any]) -> set[str]:
    return {n["id"] for n in graph["nodes"]}


def _levels(graph: dict[str, Any]) -> dict[str, int]:
    return {n["id"]: n["level"] for n in graph["nodes"]}


def test_a_leaf_is_drawn_with_the_parent_it_hangs_from() -> None:
    """An ENA attribute is a leaf. Its own view must not be a lone tag."""
    focused = _filter_graph_to_subtree(GRAPH, "attribute")

    assert _ids(focused) == {"study", "sample", "attribute"}
    assert focused["edges"], "the tag was drawn with no connection at all"


def test_a_reference_edge_does_not_drag_in_the_rest_of_the_graph() -> None:
    """`sample ┄┄> study` is a reference, not containment; following it as one
    walked back up to the study and down into every other branch."""
    focused = _filter_graph_to_subtree(GRAPH, "sample")

    assert "other_sample" not in _ids(focused)


def test_the_focused_subtree_keeps_what_it_contains() -> None:
    focused = _filter_graph_to_subtree(GRAPH, "sample")

    assert {"sample", "attribute", "run"} <= _ids(focused)


def test_levels_are_rebased_so_the_view_hangs_from_the_top() -> None:
    """vis.js lays out by level; keeping the whole graph's levels left the view
    suspended below empty ranks."""
    focused = _filter_graph_to_subtree(GRAPH, "attribute")

    assert min(_levels(focused).values()) == 0


def test_levels_keep_their_relative_depth() -> None:
    """Re-basing must shift the ranks, not flatten them."""
    levels = _levels(_filter_graph_to_subtree(GRAPH, "attribute"))

    assert levels["study"] < levels["sample"] < levels["attribute"]


def test_a_reference_between_two_kept_nodes_is_still_drawn() -> None:
    """References are not followed, but they are still shown where both ends
    are in view — that is the link a containment tree cannot express."""
    focused = _filter_graph_to_subtree(GRAPH, "sample")
    dashed = [e for e in focused["edges"] if e.get("dashes")]

    assert any(e["from"] == "run" and e["to"] == "sample" for e in dashed)


def test_focusing_the_root_still_shows_everything_under_it() -> None:
    focused = _filter_graph_to_subtree(GRAPH, "study")

    assert _ids(focused) == {"study", "sample", "attribute", "run", "other_sample"}


def test_an_unknown_node_returns_the_whole_graph() -> None:
    """A stale link renders the graph rather than an empty canvas."""
    assert _filter_graph_to_subtree(GRAPH, "gone") == GRAPH
