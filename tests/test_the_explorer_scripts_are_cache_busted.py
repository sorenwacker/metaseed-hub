"""The hub's explorer scripts carry a version, so an edit reaches the browser.

The hub serves metaseed's `explore-graph.js` and `explore-panel.js` rather than
keeping copies (see the anti-fork gate), but it pins their versions in its own
template. Those pins sat at `?v=1` and `?v=3` while the scripts changed in
metaseed, so a hub user whose browser had the page cached kept drawing the old
canvas -- the edit was live on the server and invisible in the browser.

The anti-fork gate checks the script *paths*; nothing checked that the version
moves. This is that check. It is a regression guard: it passes as the code
stands and goes red when a script reference loses its version.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "metaseed_hub"
    / "ui"
    / "templates"
    / "explore"
    / "index.html"
)

SCRIPTS = ("explore-graph.js", "explore-panel.js")


def test_every_explorer_script_is_loaded_with_a_version() -> None:
    page = TEMPLATE.read_text()

    for script in SCRIPTS:
        assert re.search(rf"{re.escape(script)}\?v=\d+", page), (
            f"{script} is loaded without a ?v= query, so a browser that has "
            "the page cached keeps running the previous version of it"
        )


def test_no_explorer_script_is_loaded_unversioned() -> None:
    """A second, unversioned tag would defeat the versioned one."""
    page = TEMPLATE.read_text()

    for script in SCRIPTS:
        for match in re.finditer(rf"{re.escape(script)}([\"'?])", page):
            assert match.group(1) == "?", (
                f"{script} is referenced without a version somewhere on the page"
            )
