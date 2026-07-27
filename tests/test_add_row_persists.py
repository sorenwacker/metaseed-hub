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


def test_add_row_flow_writes_to_facade() -> None:
    """The migrated add-row path (state.add_node skip_validation) lands in the facade.

    This is the invariant that makes the eventual facade-based serializer safe:
    a facade serialization of the state now includes the just-added row.
    """
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-1", "title": "T"})

    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    state.facade = client._facade
    state.invalidate_cache()

    # What _add_entity_list_row now does: add an incomplete draft child via the facade.
    study_model = getattr(state.facade, "Study")._model
    draft = study_model.model_construct(unique_id="STU-1", title="S")
    state.add_node("Study", draft, parent_id=inv.id, skip_validation=True)

    # Force the cache to rebuild purely from the facade. If the child were only
    # in the cache (the old bug), it would vanish here; because add_node wrote it
    # to the facade, it survives the rebuild.
    state.invalidate_cache()
    assert "Study" in [c.entity_type for c in state.entity_tree[0].children]
