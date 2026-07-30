"""Regression: a just-added table row must survive save (#54 revert, #53 redo).

PR #54 flipped ``serialize_tree`` to a facade-only serializer while the add-row
flow still appended children only to the AppState cache, so a just-added row
was silently lost on save. The add flows now write through the facade
(``state.add_node(skip_validation=True)``), which makes a facade-based save
safe. These tests pin the two halves of that contract:

- a row added through the supported add path is in the facade and in the
  serialized output;
- a node that somehow exists only in the TreeNode cache makes ``serialize_tree``
  fail loudly instead of silently dropping the node (#54's failure mode).
"""

import uuid

import pytest
from metaseed import MetaseedClient
from metaseed.ui.state import AppState, TreeNode

from metaseed_hub.ui.helpers.tree import CacheDesyncError, serialize_tree


def _entity_types(tree: list[dict]) -> list[str]:
    out: list[str] = []

    def walk(nodes: list[dict]) -> None:
        for n in nodes:
            out.append(n["entity_type"])
            walk(n.get("children", []))

    walk(tree)
    return out


def _state_with_investigation() -> tuple[AppState, str]:
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-1", "title": "T"})

    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    state.facade = client.facade
    state.invalidate_cache()
    return state, inv.id


def test_added_row_is_serialized() -> None:
    """A draft row added through the supported add path survives save."""
    state, inv_id = _state_with_investigation()

    # What _add_entity_list_row does: add an incomplete draft child via the facade.
    study_model = state.facade.Study._model
    draft = study_model.model_construct(unique_id="STU-1", title="S")
    state.add_node("Study", draft, parent_id=inv_id, skip_validation=True)

    data = serialize_tree(state)

    assert "Study" in _entity_types(data["tree"]), "a just-added row must not be lost"


def test_cache_only_node_fails_loudly_not_silently() -> None:
    """A node existing only in the TreeNode cache must not be silently dropped.

    No production flow creates such nodes anymore; if one reappears (a #54
    regression), serializing from the facade would silently lose it. The
    serializer must refuse instead, so the bug is caught at save time.
    """
    state, inv_id = _state_with_investigation()

    parent_node = state.nodes_by_id[inv_id]
    study_model = state.facade.Study._model
    child = TreeNode(
        id=str(uuid.uuid4()),
        entity_type="Study",
        instance=study_model.model_construct(unique_id="STU-1", title="S"),
        label="S",
        parent_id=inv_id,
    )
    parent_node.children.append(child)
    state.nodes_by_id[child.id] = child

    with pytest.raises(CacheDesyncError):
        serialize_tree(state)


def test_add_row_flow_writes_to_facade() -> None:
    """The add-row path (state.add_node skip_validation) lands in the facade.

    This is the invariant that makes the facade-based serializer safe: a
    facade serialization of the state includes the just-added row.
    """
    state, inv_id = _state_with_investigation()

    study_model = state.facade.Study._model
    draft = study_model.model_construct(unique_id="STU-1", title="S")
    state.add_node("Study", draft, parent_id=inv_id, skip_validation=True)

    # Force the cache to rebuild purely from the facade. If the child were only
    # in the cache (the old bug), it would vanish here; because add_node wrote it
    # to the facade, it survives the rebuild.
    state.invalidate_cache()
    assert "Study" in [c.entity_type for c in state.entity_tree[0].children]
