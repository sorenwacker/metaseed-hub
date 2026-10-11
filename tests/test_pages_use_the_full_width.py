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
    "admin-dashboard",
)


def _rules_for(css: str, selector: str) -> list[str]:
    # Anchored to the line, so ".page-header" does not also match the rules
    # a page writes for its own ".new-spec-page .page-header".
    pattern = r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}"
    return [m.group(1) for m in re.finditer(pattern, css)]


def test_no_page_container_caps_its_width() -> None:
    css = CSS.read_text()

    capped = [
        page for page in PAGES if any("max-width" in rule for rule in _rules_for(css, "." + page))
    ]

    assert capped == [], f"page containers with a max-width: {capped}"


def test_no_page_container_adds_a_gutter_of_its_own() -> None:
    """``.hub-main`` gives every page the one gutter; the specification list
    and the admin dashboard padded theirs again, so their content started
    further in than every other page's."""
    css = CSS.read_text()

    padded = [
        page
        for page in PAGES
        if any(re.search(r"(^|[^-])padding:", rule) for rule in _rules_for(css, "." + page))
    ]

    assert padded == [], f"page containers with a padding: {padded}"


def test_the_page_description_sits_under_the_title() -> None:
    """One header for every page: title, its description under it, actions on
    the right. Four pages had the description beside the title on one line,
    and SEEK carried a rule of its own to stack it."""
    css = CSS.read_text()

    (header,) = _rules_for(css, ".page-header")
    (lede,) = _rules_for(css, ".page-header > p")
    assert "flex-wrap: wrap" in header
    assert "flex-basis: 100%" in lede
    assert ".seek-page-header" not in css


def test_the_setup_guide_uses_the_width() -> None:
    """The guide's steps stopped at 46rem inside a full-width card, leaving
    the right two fifths of the page empty."""
    (steps,) = _rules_for(CSS.read_text(), ".setup-steps p")
    assert "max-width" not in steps


def test_the_repository_buttons_share_one_width_and_do_not_wrap() -> None:
    css = CSS.read_text()

    (grid,) = _rules_for(css, ".import-repositories")
    (button,) = _rules_for(css, ".import-repository .btn")
    assert "grid-template-columns: max-content" in grid
    assert "justify-self: stretch" in button and "white-space: nowrap" in button
