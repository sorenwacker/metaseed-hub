"""Two layout cues in the dataset editor.

The entities overview stacked a row's expand arrow, name and child count on
three lines: they inherit ``display: flex`` from the sidebar tree, which makes
each a block. An inline-table heading with a field note looked like any other
heading, so nobody knew to hover it.
"""

from __future__ import annotations

import re

from metaseed_hub.ui.app import STATIC_DIR

CSS = (STATIC_DIR / "css" / "hub.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    match = re.search(r"(?:^|[,}\s])" + re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_an_overview_row_keeps_arrow_name_and_count_on_one_line() -> None:
    for part in (".tree-expand", ".entity-name"):
        assert "display: inline-flex" in _rule(f".entity-overview-table td > {part}")


def test_a_heading_with_a_note_looks_hoverable() -> None:
    rule = _rule(".inline-data-table th[data-note]")
    assert "cursor: help" in rule
    assert "dotted" in rule
