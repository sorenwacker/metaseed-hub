"""A parent's form must not carry its children's inputs.

A Study saved while its Sources were listed came back with an empty title and
"title: Field required". The inline child tables sat inside the parent's
``<form>``, and a child row's inputs are named for the *child's* fields — a
Source has a title too — so they were submitted with the parent and the last
empty cell won. Other same-named columns appeared as values the user never
typed.
"""

from __future__ import annotations

import re
from pathlib import Path

FORM = Path("src/metaseed_hub/ui/templates/partials/entity_form.html")


def _between_form_tags(markup: str) -> str:
    start = markup.index("<form")
    end = markup.index("</form>")
    return markup[start:end]


def test_the_inline_tables_are_outside_the_form() -> None:
    markup = FORM.read_text()
    assert "inline_table.html" in markup, "the tables moved; update this test"
    assert "inline_table.html" not in _between_form_tags(markup), (
        "child rows inside the parent's form are submitted with it, and an "
        "empty child cell overwrites the parent's field of the same name"
    )


def test_the_form_only_names_the_entitys_own_fields() -> None:
    """Every ``name=`` inside the form comes from the entity's own fields."""
    inside = _between_form_tags(FORM.read_text())
    names = set(re.findall(r'name="([^"{}]+)"', inside))
    # The underscored names are the form's own machinery; every other input is
    # rendered from field.name. A bare "value", or a child's column name, means
    # an inline table has crept back inside the form.
    assert names <= {
        "_csrf_token",
        "_entity_type",
        "_node_id",
        "_parent_field",
        "_parent_id",
    }, f"unexpected literal input names inside the entity form: {names}"
