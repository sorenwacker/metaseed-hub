"""The hub's explorer offers the graph as a PNG.

metaseed 0.64 added ``ERD.exportPng`` to the shared ``erd-common.js``; the hub
serves that script but its own explorer page had no button calling it, so the
export was unreachable from the hub.
"""

from __future__ import annotations

from metaseed_hub.ui.app import TEMPLATES_DIR

EXPLORE_TEMPLATE = TEMPLATES_DIR / "explore" / "index.html"


def test_the_toolbar_has_a_png_button() -> None:
    template = EXPLORE_TEMPLATE.read_text()

    assert 'data-testid="export-png"' in template
    assert "ERD.exportPng(" in template


def test_the_png_carries_the_legend_the_page_shows() -> None:
    """metaseed 0.65 draws legend entries into the image; the page passes them."""
    assert "visibleLegend()" in EXPLORE_TEMPLATE.read_text()
