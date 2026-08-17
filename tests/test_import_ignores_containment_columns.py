"""A containment column is structure, not data, so an import must not store it.

The exported workbook carries a column per containment field — `studies` on
Investigation, `observation_units` on Study — holding a count rather than the
children themselves, because the children are their own sheets. Reimporting
wrote that count straight back into the field, so a list-typed field ended up
holding the string `'0'`.

Pydantic serialized it anyway and only warned, which is why it survived: ten
warnings per suite run and nothing failing. The tree was rebuilt correctly from
the sheets regardless, so the stored count was never even read — it was purely
a corrupt value riding along.

Worse, the count is not reliable: an Investigation with one Study exports
`studies` as `'0'`. Storing an unreliable number in a field meant to hold the
children is two problems, and dropping the column removes both.
"""

from __future__ import annotations

import warnings
from io import BytesIO
from typing import Any

import pytest
from metaseed import MetaseedClient
from metaseed.ui.services.export import build_workbook_from_facade

from metaseed_hub.ui.helpers import add_entities_in_order, parse_workbook_sheets
from metaseed_hub.ui.metaseed_ui import AppState


def _workbook_bytes() -> bytes:
    client = MetaseedClient("miappe", "1.1")
    investigation = client.create_entity(
        "Investigation",
        {"unique_id": "0001", "title": "SEPT1 trial"},
        skip_validation=True,
    )
    client.create_entity(
        "Study",
        {"unique_id": "st-01", "title": "field", "investigation_id": "0001"},
        parent_id=investigation.id,
        skip_validation=True,
    )
    buffer = BytesIO()
    build_workbook_from_facade(client.facade).save(buffer)
    return buffer.getvalue()


def _reimported() -> tuple[list[dict[str, Any]], AppState]:
    state = AppState(profile="miappe", version="1.1")
    facade = state.get_or_create_facade()
    imported, errors = add_entities_in_order(
        state, facade, parse_workbook_sheets(_workbook_bytes()), "Investigation"
    )
    assert imported == 2 and not errors, (imported, errors[:3])
    return MetaseedClient.from_facade(facade).serialize()["entities"], state


def _of_type(entities: list[dict[str, Any]], entity_type: str) -> dict[str, Any]:
    return next(e for e in entities if e["_type"] == entity_type)


def test_a_containment_field_does_not_receive_the_count() -> None:
    entities, _state = _reimported()

    investigation = _of_type(entities, "Investigation")
    studies = investigation.get("studies")
    assert studies != "0", "the export's count was stored where the children belong"
    assert studies is None or isinstance(studies, list)


def test_reimporting_warns_about_nothing() -> None:
    """The warning is the only symptom, so its absence is the assertion."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _reimported()

    unexpected = [str(w.message) for w in caught if "list[any]" in str(w.message)]
    assert unexpected == [], unexpected


def test_the_children_still_arrive_under_their_parent() -> None:
    """Dropping the column must not cost the tree its shape."""
    _entities, state = _reimported()

    parents = {
        node.entity_type: state.nodes_by_id[node.parent_id].entity_type
        for node in state.nodes_by_id.values()
        if node.parent_id
    }
    assert parents.get("Study") == "Investigation"


@pytest.mark.parametrize("field", ["unique_id", "title"])
def test_ordinary_columns_still_import(field: str) -> None:
    entities, _state = _reimported()

    assert _of_type(entities, "Investigation").get(field)
