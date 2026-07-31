"""The dataset load path must populate the facade, permissively (epic #53 step 1).

``ensure_dataset_facade`` is the single load path: every loaded state must hold
an authoritative facade, and loading must be at least as permissive as the old
cache-only ``deserialize_tree`` was — incomplete drafts, unknown fields, and
malformed legacy nodes must not make the whole dataset load fail. A load that
does fail must raise instead of returning an empty state, because a later save
from an empty state would overwrite the stored tree (the #54 failure mode).

The permissiveness is metaseed's, via ``client.load(..., on_skip=...)``; the
hub's own pre-pass over the payload is gone. What each dropped node was is
reported rather than swallowed, because a node that does not load is a node the
next save deletes.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from metaseed import SkippedNode

from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade


def _dataset(profile: str, version: str, data: dict[str, Any] | None) -> Any:
    """Build a minimal stand-in for a built-in-profile Dataset row."""
    return SimpleNamespace(
        id="ds-load-test",
        profile=profile,
        version=version,
        data=data,
        spec_draft_id=None,
        spec_id=None,
    )


def _tree_types(state: Any) -> dict[str, list[str]]:
    """Map each root entity type to the types of its children."""
    return {root.entity_type: [c.entity_type for c in root.children] for root in state.entity_tree}


async def _load_reporting_skips(data: dict[str, Any]) -> tuple[Any, list[SkippedNode]]:
    """Load a miappe payload, collecting what the load could not place.

    Args:
        data: The stored ``dataset.data`` payload.

    Returns:
        The loaded state and every node the load dropped, in order.
    """
    skipped: list[SkippedNode] = []
    state = await ensure_dataset_facade(
        _dataset("miappe", "1.2", data), Mock(), on_skip=skipped.append
    )
    return state, skipped


async def test_stored_tree_lands_in_the_facade() -> None:
    """A loaded state's facade holds the stored entities, not just the cache."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {"unique_id": "INV-1", "title": "T"},
                "children": [
                    {
                        "id": "stu-1",
                        "entity_type": "Study",
                        "label": "S",
                        "data": {"unique_id": "STU-1", "title": "S"},
                        "children": [],
                    }
                ],
            }
        ],
    }

    state = await ensure_dataset_facade(_dataset("miappe", "1.2", data), Mock())

    assert state.facade is not None
    roots = state.facade.get_roots()
    assert [r.entity_type for r in roots] == ["Investigation"]
    assert [c.entity_type for c in roots[0].children] == ["Study"]


async def test_incomplete_draft_with_unknown_fields_loads_without_loss() -> None:
    """Missing required fields and unknown legacy fields must not drop the node."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "draft",
                # No unique_id (required) and a field the schema does not know.
                "data": {"title": "Draft only", "legacy_field": "kept-or-ignored"},
                "children": [],
            }
        ],
    }

    state = await ensure_dataset_facade(_dataset("miappe", "1.2", data), Mock())

    node = state.entity_tree[0]
    assert node.entity_type == "Investigation"
    assert node.instance.title == "Draft only"


async def test_node_without_entity_type_is_skipped_not_fatal() -> None:
    """One malformed node must not fail the whole load (parity with the old reader)."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {"id": "bad-1", "label": "no type", "data": {"title": "x"}, "children": []},
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {"unique_id": "INV-1", "title": "T"},
                "children": [],
            },
        ],
    }

    state = await ensure_dataset_facade(_dataset("miappe", "1.2", data), Mock())

    assert [r.entity_type for r in state.facade.get_roots()] == ["Investigation"]


async def test_unknown_entity_type_is_skipped_not_fatal_and_is_reported() -> None:
    """A node whose type the schema does not define is dropped, others load.

    The drop is reported: a dataset that quietly loses an entity is the failure
    this whole path exists to prevent.
    """
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "bogus-1",
                "entity_type": "Bogus",
                "label": "?",
                "data": {"anything": 1},
                "children": [],
            },
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {"unique_id": "INV-1", "title": "T"},
                "children": [],
            },
        ],
    }

    state, skipped = await _load_reporting_skips(data)

    assert [r.entity_type for r in state.facade.get_roots()] == ["Investigation"]
    assert [s.entity_type for s in skipped] == ["Bogus"]
    assert "Bogus" in skipped[0].reason
    assert skipped[0].node["id"] == "bogus-1"


async def test_child_of_node_without_id_keeps_its_parent() -> None:
    """A legacy node missing its id gets one, so its children stay attached.

    metaseed 0.23.0 loads a node's children under the id it was actually
    created with, so a node with no stored id no longer flattens its subtree
    into roots. Asserted on the tree shape, not merely on the load succeeding.
    """
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "entity_type": "Investigation",
                "label": "T",
                "data": {"unique_id": "INV-1", "title": "T"},
                "children": [
                    {
                        "id": "stu-1",
                        "entity_type": "Study",
                        "label": "S",
                        "data": {"unique_id": "STU-1", "title": "S"},
                        "children": [],
                    }
                ],
            }
        ],
    }

    state, skipped = await _load_reporting_skips(data)

    assert _tree_types(state) == {"Investigation": ["Study"]}
    roots = state.facade.get_roots()
    assert [r.entity_type for r in roots] == ["Investigation"]
    assert [c.entity_type for c in roots[0].children] == ["Study"]
    assert skipped == []


async def test_a_non_mapping_node_is_tolerated_and_reported() -> None:
    """A hand-edited payload with a bare string where a node belongs still loads."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            "not a node at all",
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {"unique_id": "INV-1", "title": "T"},
                "children": [],
            },
        ],
    }

    state, skipped = await _load_reporting_skips(data)

    assert [r.entity_type for r in state.facade.get_roots()] == ["Investigation"]
    assert [s.entity_type for s in skipped] == [None]
    assert skipped[0].node == "not a node at all"


async def test_a_skipped_node_takes_its_subtree_and_says_how_much_was_lost() -> None:
    """The report must quantify the loss, not just name the node that caused it."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "bogus-1",
                "entity_type": "Bogus",
                "label": "?",
                "data": {},
                "children": [
                    {
                        "id": "stu-1",
                        "entity_type": "Study",
                        "label": "S",
                        "data": {"unique_id": "STU-1"},
                        "children": [],
                    }
                ],
            }
        ],
    }

    state, skipped = await _load_reporting_skips(data)

    # The child is dropped with its parent, not re-parented to a root: the
    # payload never said it was one.
    assert state.facade.get_roots() == []
    assert len(skipped) == 1
    assert skipped[0].descendants_dropped == 1


async def test_a_clean_load_reports_nothing() -> None:
    """No skips means no reports; a well-formed dataset must stay quiet."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [
            {
                "id": "inv-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {"unique_id": "INV-1", "title": "T"},
                "children": [
                    {
                        "id": "stu-1",
                        "entity_type": "Study",
                        "label": "S",
                        "data": {"unique_id": "STU-1", "title": "S"},
                        "children": [],
                    }
                ],
            }
        ],
    }

    state, skipped = await _load_reporting_skips(data)

    assert skipped == []
    assert _tree_types(state) == {"Investigation": ["Study"]}


async def test_a_skip_is_logged_with_the_dataset_id_even_without_a_collector(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Callers that ask for no report still leave a trace an operator can find."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "tree": [{"id": "bogus-1", "entity_type": "Bogus", "label": "?", "children": []}],
    }

    with caplog.at_level("WARNING", logger="metaseed_hub"):
        await ensure_dataset_facade(_dataset("miappe", "1.2", data), Mock())

    assert any("ds-load-test" in r.message and "Bogus" in r.message for r in caplog.records)


async def test_legacy_flat_payload_still_loads() -> None:
    """Flat ``{entities: [...]}`` payloads (old MCP writes) keep loading."""
    data = {
        "profile": "miappe",
        "version": "1.2",
        "entities": [
            {"_type": "Investigation", "unique_id": "INV-1", "title": "T"},
        ],
    }

    state = await ensure_dataset_facade(_dataset("miappe", "1.2", data), Mock())

    assert [r.entity_type for r in state.facade.get_roots()] == ["Investigation"]


async def test_unloadable_data_raises_instead_of_empty_state() -> None:
    """If stored entities cannot be loaded, raise; never return an empty state.

    An empty state here is the #54 failure mode: the next save would serialize
    the empty facade and overwrite the stored tree.
    """
    from metaseed_hub.ui.services.exceptions import DatasetDataLoadError

    data = {
        "profile": "no-such-profile",
        "version": "9.9",
        "tree": [
            {
                "id": "n-1",
                "entity_type": "Investigation",
                "label": "T",
                "data": {"title": "T"},
                "children": [],
            }
        ],
    }

    with pytest.raises(DatasetDataLoadError):
        await ensure_dataset_facade(_dataset("no-such-profile", "9.9", data), Mock())
