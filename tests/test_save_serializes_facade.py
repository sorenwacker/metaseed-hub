"""The save path serializes the facade, the source of truth (epic #53 step 2).

``save_dataset_state`` persists via ``serialize_tree``, which must delegate to
metaseed's ``MetaseedClient.serialize(format="tree")`` so the hub has exactly
one write format. That is only safe because every mutation flow writes through
the facade; these tests pin both properties: serializer equality, and that a
row added by each UI flow (entity-list row, primitive-list row, nested child)
is present in the serialized output.
"""

from typing import Any

from metaseed import MetaseedClient
from metaseed.ui.state import AppState

from metaseed_hub.ui.helpers.tree import (
    add_entity_node,
    create_nested_nodes,
    make_json_serializable,
    serialize_tree,
)


def _state_with_investigation() -> tuple[AppState, Any]:
    """AppState over a facade holding one valid Investigation."""
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-1", "title": "T"})
    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    state.facade = client.facade
    state.invalidate_cache()
    return state, inv


def _flat_nodes(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            out.append(n)
            walk(n.get("children", []))

    walk(tree)
    return out


def test_serialize_tree_is_the_client_serializer() -> None:
    """The hub serializer's output IS the client serializer's output."""
    state, _ = _state_with_investigation()
    node = add_entity_node(state, "Study", {"unique_id": "STU-1", "title": "S"})
    create_nested_nodes(
        state,
        state.facade,
        node,
        "Study",
        {"persons": [{"name": "P"}]},
    )

    expected = make_json_serializable(
        MetaseedClient.from_facade(state.facade).serialize(format="tree")
    )

    assert serialize_tree(state) == expected


def test_entity_list_row_survives_facade_save() -> None:
    """An incomplete draft row added by the add-row flow is serialized."""
    state, inv = _state_with_investigation()

    # What _add_entity_list_row does: an incomplete draft child via the facade.
    study_model = state.facade.Study._model
    draft = study_model.model_construct(unique_id="STU-1")
    state.add_node("Study", draft, parent_id=inv.id, skip_validation=True)

    nodes = _flat_nodes(serialize_tree(state)["tree"])
    studies = [n for n in nodes if n["entity_type"] == "Study"]
    assert [s["data"].get("unique_id") for s in studies] == ["STU-1"]


def test_primitive_list_row_survives_facade_save() -> None:
    """A primitive list value added by the add-row flow is serialized."""
    state, inv = _state_with_investigation()

    # What add_table_row does for primitive lists: rebuild the parent instance
    # with the extended list and write it back through the facade.
    parent = state.nodes_by_id[inv.id]
    update_data = parent.instance.model_dump(exclude_none=True)
    update_data["associated_publications"] = ["https://doi.org/10.1000/x"]
    helper = state.facade.Investigation
    state.update_node(inv.id, helper._model.model_construct(**update_data))

    tree = serialize_tree(state)["tree"]
    assert tree[0]["data"]["associated_publications"] == ["https://doi.org/10.1000/x"]


def test_nested_child_survives_facade_save() -> None:
    """A child created by create_nested_nodes (import/example flow) is serialized."""
    state, _ = _state_with_investigation()
    parent = state.entity_tree[0]

    create_nested_nodes(
        state,
        state.facade,
        parent,
        "Investigation",
        {"studies": [{"unique_id": "STU-9", "title": "Nested"}]},
    )

    nodes = _flat_nodes(serialize_tree(state)["tree"])
    studies = [n for n in nodes if n["entity_type"] == "Study"]
    assert [s["data"].get("unique_id") for s in studies] == ["STU-9"]
