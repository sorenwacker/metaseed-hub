"""A workbook row goes under the record its Belongs to names, in the stated field.

metaseed 0.67 writes **Belongs to** (``Type: identifier``) and reads it into
``_parent_unique_id`` and ``_parent_field``. The hub places imported rows itself
(``add_entities_in_order``) and matched the identifier alone, keeping the first
record that carried it, so a study's person landed under an investigation that
happened to share the study's identifier.
"""

from __future__ import annotations

from typing import Any

from metaseed import MetaseedClient

from metaseed_hub.ui.helpers import add_entities_in_order
from metaseed_hub.ui.metaseed_ui import AppState


def _placed(person_field: str) -> dict[str, Any]:
    state = AppState(profile="miappe", version="1.2")
    facade = state.get_or_create_facade()
    rows = {
        "Investigation": [{"_type": "Investigation", "unique_id": "X-1", "title": "Trial"}],
        "Study": [
            {
                "_type": "Study",
                "unique_id": "X-1",
                "title": "Field study",
                "investigation_id": "X-1",
                "_parent_unique_id": "X-1",
                "_parent_field": "studies",
            }
        ],
        "Person": [
            {
                "_type": "Person",
                "name": "P",
                "_parent_unique_id": "X-1",
                "_parent_field": person_field,
            }
        ],
    }
    imported, errors = add_entities_in_order(state, facade, rows, "Investigation")
    assert imported == 3 and not errors, errors
    client = MetaseedClient.from_facade(facade)
    person = next(e for e in client.serialize()["entities"] if e["_type"] == "Person")
    parent = client.get_entity(client.get_entity(person["_node_id"]).parent_id)
    return {"parent": parent.entity_type, "field": person.get("_parent_field")}


def test_a_study_person_goes_under_the_study() -> None:
    assert _placed("persons") == {"parent": "Study", "field": "persons"}


def test_an_investigation_contact_goes_under_the_investigation() -> None:
    assert _placed("contacts") == {"parent": "Investigation", "field": "contacts"}
