"""serialize_tree reuses metaseed's client serializer (issue #51 / epic #53).

The hub's save path (``save_dataset_state`` -> ``serialize_tree``) now delegates
to ``MetaseedClient.serialize(format="tree")`` via the state's facade, so there
is one serializer. These tests mirror the real save flow (facade populated, as
``ensure_dataset_facade`` does) and confirm the output matches the client and
round-trips.
"""

from metaseed import MetaseedClient
from metaseed.ui.state import AppState

from metaseed_hub.ui.helpers.tree import serialize_tree


def _flatten(tree: list[dict]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()

    def walk(nodes: list[dict]) -> None:
        for n in nodes:
            out.add((n["entity_type"], repr(sorted(n.get("data", {}).items()))))
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


def _state_from_client(client: MetaseedClient) -> AppState:
    """Bridge a populated client facade onto an AppState, as the hub save flow does."""
    state = AppState()
    state.profile = client.profile
    state.version = "1.2"
    state.facade = client._facade
    state.invalidate_cache()
    return state


def test_serialize_tree_matches_client_serializer() -> None:
    """serialize_tree produces the same tree the client serializer does."""
    client = _client_with_tree()
    state = _state_from_client(client)

    data = serialize_tree(state)

    assert _flatten(data["tree"]) == _flatten(client.serialize(format="tree")["tree"])
    node = data["tree"][0]
    assert {"id", "entity_type", "label", "data", "children"} <= set(node)


def test_saved_tree_reloads_with_all_entities() -> None:
    """The serialized tree loads back into a fresh client with entities intact."""
    client = _client_with_tree()
    state = _state_from_client(client)

    data = serialize_tree(state)

    reloaded = MetaseedClient("miappe", "1.2")
    reloaded.load(data)
    assert _flatten(reloaded.serialize(format="tree")["tree"]) == _flatten(data["tree"])


def test_no_facade_serializes_to_empty_tree() -> None:
    """A degenerate state with no facade yields an empty tree, not an error."""
    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"

    assert serialize_tree(state) == {"profile": "miappe", "version": "1.2", "tree": []}
