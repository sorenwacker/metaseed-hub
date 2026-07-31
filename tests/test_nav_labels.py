"""The navigation label for the spec builder, pinned across every template.

Three templates rendered a link to the same destination under three different
names ("Specs", "Spec Builder", "Builder"), and prose elsewhere told people to
click a name the header did not show. The label is part of the interface, so it
is asserted rather than left to whoever edits a template next. metaseed's own
header calls it "Builder"; the hub matches it, so moving between the two
applications does not rename the same tool.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / "src" / "metaseed_hub" / "ui" / "templates"
DOCS = Path(__file__).parent.parent / "docs"

NAV_LABEL = "Builder"

# <a ...href="/hub/spec-builder"...>LABEL</a>, the destination the header links to.
NAV_LINK = re.compile(
    r'<a[^>]*href="/hub/spec-builder"[^>]*class="[^"]*nav-(?:btn|item)[^"]*"[^>]*>([^<]+)</a>'
)


def _nav_links() -> list[tuple[Path, str]]:
    """Every navigation link to the spec builder, with the label it renders."""
    found: list[tuple[Path, str]] = []
    for template in TEMPLATES.rglob("*.html"):
        for label in NAV_LINK.findall(template.read_text()):
            found.append((template, label.strip()))
    return found


def test_the_spec_builder_is_called_builder_in_every_navigation() -> None:
    """One destination, one name -- in the header and in the builder's own nav."""
    links = _nav_links()
    assert links, "no navigation link to /hub/spec-builder was found"

    wrong = [(path.name, label) for path, label in links if label != NAV_LABEL]
    assert not wrong, f"navigation labels that are not {NAV_LABEL!r}: {wrong}"


def test_prose_does_not_send_people_to_a_label_the_header_lacks() -> None:
    """Instructions naming the header item must name the item that exists."""
    stale = re.compile(r"\*\*Specs\*\*\s+in the (?:top bar|header)")

    for page in list(TEMPLATES.rglob("*.html")) + list(DOCS.rglob("*.md")):
        assert not stale.search(page.read_text()), (
            f"{page.name} tells people to click 'Specs' in the header, "
            f"which is labelled {NAV_LABEL!r}"
        )
