"""The Actions column of an inline table is as wide as its two icons.

metaseed's stylesheet gives every inline-table cell ``min-width: 18ch`` so data
columns stay readable; the Actions column inherited it and took 150 px or more
for an edit and a delete icon. ``td.row-actions`` was also ``display: flex``,
which takes a cell out of the table layout and misaligns its row borders.
"""

from __future__ import annotations

import re

from metaseed_hub.ui.app import STATIC_DIR

CSS = (STATIC_DIR / "css" / "hub.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    match = re.search(r"(?:^|[,}\s])" + re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_the_actions_cells_shrink_to_their_content() -> None:
    rule = _rule(".inline-data-table td.row-actions")
    assert "min-width: 0" in rule
    assert "width: 1%" in rule


def test_the_actions_cell_stays_a_table_cell() -> None:
    """``.row-actions`` lays out the icons as flex; the cell must override it."""
    assert "display: table-cell" in _rule("td.row-actions")
