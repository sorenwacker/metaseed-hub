"""A column heading in an inline table says what a valid value is, on hover.

The exported Excel heading carries a note built from the field's specification
(metaseed #305); the editor's inline tables showed the bare name, so a person
typing into ``SUBJECT ID*`` had no description and no format. The note comes
from the one builder metaseed has, so the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path

from metaseed import MetaseedClient
from metaseed.specs.loader import SpecLoader
from metaseed.specs.schema import applies_to_entity

from metaseed_hub.ui.helpers.tables import build_inline_tables
from metaseed_hub.ui.metaseed_ui import AppState, heading_note

TEMPLATE = (
    Path(__file__).resolve().parents[1] / "src/metaseed_hub/ui/templates/partials/inline_table.html"
)


def _state() -> tuple[AppState, str]:
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-1", "title": "T"})
    client.create_entity(
        "Study",
        {"unique_id": "STU-1", "investigation_id": "INV-1", "title": "S"},
        parent_id=inv.id,
        skip_validation=True,
    )
    state = AppState()
    state.profile, state.version, state.facade = "miappe", "1.2", client.facade
    state.invalidate_cache()
    return state, inv.id


def test_each_column_carries_the_note_the_exported_heading_carries() -> None:
    state, inv_id = _state()

    notes = build_inline_tables(state, inv_id, [{"name": "studies", "item_type": "Study"}])[
        "studies"
    ]["column_notes"]

    spec = SpecLoader().load_profile("1.2", "miappe")
    field = next(f for f in spec.entities["Study"].fields if f.name == "unique_id")
    rules = [r for r in spec.validation_rules if applies_to_entity(r.applies_to, "Study")]
    assert notes["unique_id"] == heading_note("unique_id", field, rules)
    assert "required" in notes["unique_id"].lower()


def test_the_heading_shows_the_note_on_hover() -> None:
    assert 'title="{{ column_notes[col] }}"' in TEMPLATE.read_text(encoding="utf-8")
