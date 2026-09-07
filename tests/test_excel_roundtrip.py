"""A dataset must survive the hub's Excel export and import unchanged.

The import existed but flattened the tree: every ``_``-prefixed key was
stripped and every entity created parentless. The export now writes a
``_parent`` column and the import files each child under the node whose
declared identifier the column names.
"""

from __future__ import annotations

from io import BytesIO

from metaseed import MetaseedClient

from metaseed_hub.ui.helpers.entity_import import add_entities_in_order
from metaseed_hub.ui.helpers.uploads import parse_workbook_sheets
from metaseed_hub.ui.metaseed_ui import AppState
from metaseed_hub.ui.services.export import build_workbook


def _workbook_bytes() -> bytes:
    client = MetaseedClient("miappe", "1.1")
    inv = client.create_entity(
        "Investigation",
        {"unique_id": "0001", "title": "SEPT1 trial"},
        skip_validation=True,
    )
    client.create_entity(
        "Study",
        {"unique_id": "st-01", "title": "field", "investigation_id": "0001"},
        parent_id=inv.id,
        skip_validation=True,
    )
    workbook = build_workbook(client.facade)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _reimport(raw: bytes) -> list[dict]:
    state = AppState(profile="miappe", version="1.1")
    facade = state.get_or_create_facade()
    imported, errors = add_entities_in_order(
        state,
        facade,
        parse_workbook_sheets(raw, profile="miappe", version="1.1", facade=facade),
        "Investigation",
    )
    assert imported == 2 and not errors, (imported, errors[:3])
    return MetaseedClient.from_facade(facade).serialize()["entities"]


def test_the_tree_survives_the_round_trip() -> None:
    entities = _reimport(_workbook_bytes())
    study = next(e for e in entities if e["_type"] == "Study")
    assert study["_parent_unique_id"] == "0001", (
        "the imported Study is an orphan — the _parent column was not honored"
    )


def test_the_fragile_values_survive() -> None:
    entities = _reimport(_workbook_bytes())
    inv = next(e for e in entities if e["_type"] == "Investigation")
    assert inv["unique_id"] == "0001"  # leading zeros
    assert inv["title"] == "SEPT1 trial"  # gene-name-becomes-a-date


def test_an_unmatched_parent_is_reported_not_flattened() -> None:
    """A child whose _parent names nothing must say so, not silently re-root.

    node_by_identifier.get(missing) returned None and the child was created
    at the root — the tree quietly changed shape and the import still
    reported success.
    """
    state = AppState(profile="miappe", version="1.1")
    facade = state.get_or_create_facade()

    imported, errors = add_entities_in_order(
        state,
        facade,
        {
            "Investigation": [{"unique_id": "0001", "title": "T"}],
            "Study": [
                {
                    "unique_id": "st-01",
                    "title": "field",
                    "investigation_id": "0001",
                    "_parent": "no-such-identifier",
                }
            ],
        },
        "Investigation",
    )

    assert imported == 2, "the child still imports — losing it would be worse"
    assert any("no-such-identifier" in e for e in errors), errors
