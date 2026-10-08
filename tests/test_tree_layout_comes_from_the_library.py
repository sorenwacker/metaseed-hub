"""The Tree layout is metaseed's, and every graph of a specification offers it.

The hub's Builder had its own ``toggleHierarchicalLayout``. It read
``window.ERD``, which a top-level ``const ERD`` does not create, so the button
did nothing; and the Explorer, which draws the same kind of graph, had no
button. The toggle is defined in metaseed's ``erd-common.js``, which both
pages load. See docs/explorer.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui"
TOOLBARS = [
    UI / "templates" / "spec_builder" / "base.html",
    UI / "templates" / "explore" / "index.html",
]


def test_the_hub_defines_no_toggle_of_its_own() -> None:
    own = [
        path.name
        for path in sorted((UI / "static" / "js").glob("*.js"))
        if "function toggleHierarchicalLayout" in path.read_text()
    ]

    assert own == []


def test_the_library_script_the_pages_load_defines_it() -> None:
    from metaseed_hub.ui.metaseed_ui import METASEED_STATIC_DIR

    shared = (Path(METASEED_STATIC_DIR) / "js" / "erd-common.js").read_text()

    assert "function toggleHierarchicalLayout" in shared


@pytest.mark.parametrize("toolbar", TOOLBARS, ids=lambda path: path.parent.name)
def test_every_spec_graph_offers_tree_beside_layout(toolbar: Path) -> None:
    page = toolbar.read_text()

    assert "/hub/static/js/erd-common.js" in page
    assert 'onclick="autoLayout()"' in page
    assert 'onclick="toggleHierarchicalLayout()"' in page
    assert 'data-testid="btn-tree-layout"' in page
