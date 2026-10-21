"""A column with allowed values is edited with a dropdown of those values.

The heading note listed ``Allowed values: SINGLE, PAIRED`` while the cell was a
free text box, so the table said what to type and then let anything be typed.
"""

from __future__ import annotations

import re

from jinja2 import Environment, FileSystemLoader
from metaseed import MetaseedClient

from metaseed_hub.ui.app import TEMPLATES_DIR
from metaseed_hub.ui.helpers.tables import build_inline_tables
from metaseed_hub.ui.helpers.text import humanize_field_name
from metaseed_hub.ui.metaseed_ui import AppState


def _cells() -> dict[str, str]:
    client = MetaseedClient("ena", "1.0")
    study = client.create_entity("Study", {"alias": "test-study"}, skip_validation=True)
    client.create_entity(
        "Experiment",
        {"alias": "test-exp", "library_layout": "PAIRED"},
        parent_id=study.id,
        skip_validation=True,
    )
    state = AppState()
    state.profile, state.version, state.facade = "ena", "1.0", client.facade
    state.invalidate_cache()
    field = {"name": "experiments", "item_type": "Experiment"}
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["humanize"] = humanize_field_name
    html = env.get_template("partials/inline_table.html").render(
        field=field,
        inline_tables=build_inline_tables(state, study.id, [field]),
        dataset_id="d",
        node_id=study.id,
    )
    return dict(re.findall(r'<td class="editable-cell" data-col="([^"]+)">(.*?)</td>', html, re.S))


def test_an_enumerated_column_is_a_dropdown_of_its_values() -> None:
    cell = _cells()["library_layout"]

    assert "<select" in cell
    assert re.findall(r'<option value="([^"]*)"', cell) == ["", "SINGLE", "PAIRED"]
    assert re.search(r'<option value="PAIRED"\s+selected', cell), "the stored value is not selected"


def test_a_free_text_column_stays_a_text_box() -> None:
    assert "<select" not in _cells()["alias"]
