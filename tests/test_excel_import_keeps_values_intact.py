"""An exported workbook must reimport as the values it was exported from.

The hub parsed workbooks itself instead of calling metaseed's
``workbook_to_payload``, and the copy was behind the library in two ways that
changed data on every round trip:

- the export prefixes a formula-triggering cell with a quote so Excel shows it
  as text, and the hub never removed the quote, so a study titled ``=cmd|calc``
  came back as ``'=cmd|calc`` and kept the quote forever;
- the export joins a scalar list into one cell, and the hub never split it, so
  ``["block", "plot"]`` came back as the string ``"block, plot"`` — a string in
  a list field, which is the same defect #129 was filed for on the containment
  columns, in a field no test looked at.

The library's parser does both correctly. These tests are the reason the hub
must not parse workbooks itself.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from metaseed import MetaseedClient
from metaseed.ui.services.export import build_workbook_from_facade

from metaseed_hub.ui.helpers import add_entities_in_order, parse_workbook_sheets
from metaseed_hub.ui.metaseed_ui import AppState

#: A value Excel would evaluate as a formula, so the export escapes it.
FORMULA_TITLE = "=cmd|calc"

#: A scalar list field on MIAPPE Study, which the export joins into one cell.
HIERARCHY = ["block", "plot"]


def _workbook_bytes() -> bytes:
    client = MetaseedClient("miappe", "1.1")
    investigation = client.create_entity(
        "Investigation",
        {"unique_id": "0001", "title": "SEPT1 trial"},
        skip_validation=True,
    )
    client.create_entity(
        "Study",
        {
            "unique_id": "st-01",
            "title": FORMULA_TITLE,
            "investigation_id": "0001",
            "observation_unit_level_hierarchy": HIERARCHY,
        },
        parent_id=investigation.id,
        skip_validation=True,
    )
    buffer = BytesIO()
    build_workbook_from_facade(client.facade).save(buffer)
    return buffer.getvalue()


def _parsed(facade: Any) -> dict[str, list[dict[str, Any]]]:
    return parse_workbook_sheets(_workbook_bytes(), profile="miappe", version="1.1", facade=facade)


def _reimported_study() -> dict[str, Any]:
    state = AppState(profile="miappe", version="1.1")
    facade = state.get_or_create_facade()
    imported, errors = add_entities_in_order(state, facade, _parsed(facade), "Investigation")
    assert imported == 2 and not errors, (imported, errors[:3])
    entities = MetaseedClient.from_facade(facade).serialize()["entities"]
    return next(e for e in entities if e["_type"] == "Study")


def test_a_formula_escaped_value_loses_its_escape_on_the_way_back() -> None:
    """The quote is the export's, not the user's; keeping it changes the data."""
    assert _reimported_study().get("title") == FORMULA_TITLE


def test_a_scalar_list_comes_back_as_a_list() -> None:
    study = _reimported_study()

    assert study.get("observation_unit_level_hierarchy") == HIERARCHY


def test_reimporting_a_scalar_list_warns_about_nothing() -> None:
    """The same `list[any]` warning as #129, in a field its fix did not cover."""
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _reimported_study()

    unexpected = [str(w.message) for w in caught if "list[any]" in str(w.message)]
    assert unexpected == [], unexpected


def test_the_tree_still_survives_the_round_trip() -> None:
    """Parsing differently must not cost the import its parent links."""
    state = AppState(profile="miappe", version="1.1")
    facade = state.get_or_create_facade()
    add_entities_in_order(state, facade, _parsed(facade), "Investigation")

    parents = {
        node.entity_type: state.nodes_by_id[node.parent_id].entity_type
        for node in state.nodes_by_id.values()
        if node.parent_id
    }
    assert parents.get("Study") == "Investigation"
