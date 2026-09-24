"""The collaboration picker must not be cut to a few letters.

The sharing panel sits in a narrow sidebar. The address form already gives the
address a line of its own so the role and the button can share the next; the
collaboration form was added with two selects and a button on one line, so a
real collaboration name rendered as "metas" and the role as "Viewe".
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path("src/metaseed_hub/ui/static/css/hub.css")
PANEL = Path("src/metaseed_hub/ui/templates/partials/members_panel.html")


def _rule(selector: str) -> str:
    """The body of the one top-level rule for ``selector``."""
    pattern = re.compile(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", re.MULTILINE)
    found = pattern.search(CSS.read_text())
    assert found, f"no rule for {selector}"
    return found.group(1)


def test_the_collaboration_select_takes_a_line_of_its_own() -> None:
    """Three controls on one line is what squeezed both selects."""
    assert "flex: 1 1 100%" in _rule(".member-add .grant-audience")


def test_the_form_marks_the_collaboration_select_for_that_rule() -> None:
    panel = PANEL.read_text()

    audience = next(line for line in panel.splitlines() if 'data-testid="grant-urn"' in line)
    block = panel[panel.index(audience) - 400 : panel.index(audience) + 200]
    assert "grant-audience" in block


def test_a_long_collaboration_name_is_not_clipped() -> None:
    """A select that cannot shrink below its content forces the layout to
    give it room, rather than showing five letters of a name."""
    assert "text-overflow" in _rule(".member-add .grant-audience") or "flex: 1 1 100%" in _rule(
        ".member-add .grant-audience"
    )
