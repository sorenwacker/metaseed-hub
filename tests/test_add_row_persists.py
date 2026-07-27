"""Regression: a table row added to the AppState cache must survive save (#54 revert).

The "add entity row" flow (`_add_entity_list_row`) appends the new child to the
AppState cache (parent_node.children / nodes_by_id) to allow an incomplete draft
row, deliberately bypassing facade validation. serialize_tree MUST include such
cache-added children, or a just-added row is silently lost on save. This test
fails against a facade-only serializer.
"""

import uuid

from metaseed import MetaseedClient
from metaseed.ui.state import AppState, TreeNode

from metaseed_hub.ui.helpers.tree import serialize_tree


def _entity_types(tree: list[dict]) -> list[str]:
    out: list[str] = []

    def walk(nodes: list[dict]) -> None:
        for n in nodes:
            out.append(n["entity_type"])
            walk(n.get("children", []))

    walk(tree)
    return out


def test_cache_added_child_is_serialized() -> None:
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-1", "title": "T"})

    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    state.facade = client._facade
    state.invalidate_cache()

    # Mimic _add_entity_list_row: add an (incomplete) child straight to the cache.
    parent_node = state.nodes_by_id[inv.id]
    study_model = getattr(state.facade, "Study")._model
    child = TreeNode(
        id=str(uuid.uuid4()),
        entity_type="Study",
        instance=study_model.model_construct(unique_id="STU-1", title="S"),
        label="S",
        parent_id=inv.id,
    )
    parent_node.children.append(child)
    state.nodes_by_id[child.id] = child

    data = serialize_tree(state)

    assert "Study" in _entity_types(data["tree"]), "a cache-added row must not be lost"
