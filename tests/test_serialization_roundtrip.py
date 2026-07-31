"""Round-trip of the single dataset serializer through the hub load path (#51).

``serialize_tree`` delegates to metaseed's ``MetaseedClient.serialize
(format="tree")`` and the hub loads ``dataset.data`` with
``MetaseedClient.load``. This pins that a saved payload reloads and
reserializes identically, so the write and read paths cannot silently diverge.
"""

from metaseed import MetaseedClient
from metaseed.ui.state import AppState

from metaseed_hub.ui.helpers.tree import serialize_tree


def _client_with_tree() -> MetaseedClient:
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-001", "title": "Drought"})
    client.create_entity(
        "Study",
        {"unique_id": "STU-001", "title": "S1", "investigation_id": "INV-001"},
        parent_id=inv.id,
    )
    return client


def _reload(data: dict) -> AppState:
    """Load a stored payload the way ensure_dataset_facade does."""
    client = MetaseedClient("miappe", "1.2")
    skipped: list = []
    client.load(data, on_skip=skipped.append)
    assert skipped == [], skipped
    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    state.facade = client.facade
    state.invalidate_cache()
    return state


def test_saved_payload_reloads_and_reserializes_identically() -> None:
    """save -> load -> save is the identity on the stored payload."""
    client = _client_with_tree()

    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    state.facade = client.facade
    state.invalidate_cache()

    data_a = serialize_tree(state)
    data_b = serialize_tree(_reload(data_a))

    assert data_a == data_b


def test_saved_payload_uses_the_tree_envelope() -> None:
    """The stored payload is the {profile, version, tree} envelope."""
    client = _client_with_tree()

    state = AppState()
    state.profile = "miappe"
    state.version = "1.2"
    state.facade = client.facade
    state.invalidate_cache()

    data = serialize_tree(state)

    assert set(data) >= {"profile", "version", "tree"}
    assert data["profile"] == "miappe"
    node = data["tree"][0]
    assert {"id", "entity_type", "label", "data", "children"} <= set(node)
    assert [c["entity_type"] for c in node["children"]] == ["Study"]
