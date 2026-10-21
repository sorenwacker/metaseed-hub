"""An inline table marks its ontology-term columns with an icon.

A cell of an ontology-term column opens the ontology search, but nothing in the
table said which columns do: the form shows a badge and a Search button, the
table showed a plain heading.
"""

from __future__ import annotations

import re

from jinja2 import Environment, FileSystemLoader
from metaseed import MetaseedClient

from metaseed_hub.ui.app import TEMPLATES_DIR
from metaseed_hub.ui.helpers.tables import build_inline_tables
from metaseed_hub.ui.helpers.text import humanize_field_name
from metaseed_hub.ui.metaseed_ui import AppState


def _headings() -> dict[str, str]:
    client = MetaseedClient("miappe", "1.2")
    inv = client.create_entity("Investigation", {"unique_id": "INV-1", "title": "T"})
    study = client.create_entity(
        "Study",
        {"unique_id": "STU-1", "investigation_id": "INV-1", "title": "S"},
        parent_id=inv.id,
        skip_validation=True,
    )
    state = AppState()
    state.profile, state.version, state.facade = "miappe", "1.2", client.facade
    state.invalidate_cache()
    field = {"name": "observed_variables", "item_type": "ObservedVariable"}
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters["humanize"] = humanize_field_name
    html = env.get_template("partials/inline_table.html").render(
        field=field,
        inline_tables=build_inline_tables(state, study.id, [field]),
        dataset_id="d",
        node_id=study.id,
    )
    return dict(re.findall(r'<th data-col="([^"]+)"[^>]*>(.*?)</th>', html, re.S))


def test_an_ontology_column_heading_carries_the_icon() -> None:
    headings = _headings()

    assert "ontology-icon" in headings["trait_accession_number"]


def test_a_plain_column_heading_does_not() -> None:
    headings = _headings()

    assert "ontology-icon" not in headings["name"]
