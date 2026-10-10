"""Pages use the full width of the window.

The owner: "in general I like using the full available screen". SEEK sat in
a 52rem column, Notifications in 48rem, New Dataset in 900px, the Builder's
start page centred in 800px, the specification list, Explorer and Compare
each under a cap of their own. A page container caps nothing now; only a
block of legal prose keeps a reading column.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "metaseed_hub"
    / "ui"
    / "static"
    / "css"
    / "hub.css"
)

#: Every class a page's outermost element carries.
PAGES = (
    "seek-page",
    "notifications-page",
    "new-dataset-page",
    "new-spec-page",
    "spec-list-page",
    "explore-page",
    "compare-page",
    "overview-page",
    "editor-welcome",
)


def _rules_for(css: str, selector: str) -> list[str]:
    return [m.group(1) for m in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", css)]


def test_no_page_container_caps_its_width() -> None:
    css = CSS.read_text()

    capped = [
        page for page in PAGES if any("max-width" in rule for rule in _rules_for(css, "." + page))
    ]

    assert capped == [], f"page containers with a max-width: {capped}"


def test_the_repository_buttons_share_one_width_and_do_not_wrap() -> None:
    css = CSS.read_text()

    (grid,) = _rules_for(css, ".import-repositories")
    (button,) = _rules_for(css, ".import-repository .btn")
    assert "grid-template-columns: max-content" in grid
    assert "justify-self: stretch" in button and "white-space: nowrap" in button
