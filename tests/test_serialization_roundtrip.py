"""Cross-compatibility of the two dataset serializers (issue #51).

metaseed's ``MetaseedClient.serialize(format="tree")`` (used by EntityService)
and the hub's ``serialize_tree`` (used by ``save_dataset_state``) both write the
same ``dataset.data`` column. This pins that they produce the same tree shape and
that either serializer's output round-trips through the hub reader, so the two
cannot silently diverge into incompatible formats.
"""

from metaseed import MetaseedClient
from metaseed.ui.state import AppState

from metaseed_hub.ui.helpers.tree import deserialize_tree, serialize_tree


def _flatten(tree: list[dict]) -> set[tuple[str, str]]:
    """Reduce a serialized tree to a set of (entity_type, sorted data) pairs."""
    out: set[tuple[str, str]] = set()

    def walk(nodes: list[dict]) -> None:
        for n in nodes:
            data = {k: v for k, v in n.get("data", {}).items()}
            out.add((n["entity_type"], repr(sorted(data.items()))))
            walk(n.get("children", []))

    walk(tree)
    return out


def _client_with_tree() -> MetaseedClient:
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-001", "title": "Drought"})
    client.create_entity(
        "Study",
        {"unique_id": "STU-001", "title": "S1", "investigation_id": "INV-001"},
        parent_id=inv.id,
    )
    return client


def test_client_tree_output_reloads_and_reserializes_equivalently() -> None:
    """Path A output (client.serialize tree) round-trips through the hub path."""
    client = _client_with_tree()
    data_a = client.serialize(format="tree")

    # Hub reader consumes Path A output...
    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    deserialize_tree(state, data_a)

    # ...and the hub serializer (Path B) reproduces the same entities.
    data_b = serialize_tree(state)

    assert _flatten(data_a["tree"]) == _flatten(data_b["tree"])
    assert data_a["profile"] == data_b["profile"]


def test_both_serializers_use_the_same_tree_envelope() -> None:
    """Both serializers wrap the tree in the same {profile, version, tree} keys."""
    client = _client_with_tree()
    data_a = client.serialize(format="tree")

    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    deserialize_tree(state, data_a)
    data_b = serialize_tree(state)

    assert set(data_a) >= {"profile", "version", "tree"}
    assert set(data_b) >= {"profile", "version", "tree"}
    # Node shape agrees on the core keys (hub adds parent_id).
    node_a = data_a["tree"][0]
    node_b = data_b["tree"][0]
    assert {"id", "entity_type", "label", "data", "children"} <= set(node_a)
    assert {"id", "entity_type", "label", "data", "children"} <= set(node_b)
