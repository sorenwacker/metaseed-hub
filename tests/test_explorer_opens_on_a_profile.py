"""The Explorer opens on the first profile instead of an empty canvas.

A first visit showed a grid with a one-line hint and nothing to read until a
profile was chosen and Explore clicked.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE = Path("src/metaseed_hub/ui/templates/explore/index.html")


def test_the_first_profile_is_explored_on_load() -> None:
    page = TEMPLATE.read_text()
    body = page[page.index("function exploreFirstProfile") :]
    body = body[: body.index("\n}\n")]
    assert "base.selectedIndex = 1" in body
    assert "updateVersions('base')" in body
    assert "runComparison()" in body
    assert "addEventListener('DOMContentLoaded', exploreFirstProfile)" in page


def test_a_profile_already_chosen_is_kept() -> None:
    page = TEMPLATE.read_text()
    body = page[page.index("function exploreFirstProfile") :]
    assert "if (base.value" in body[: body.index("\n}\n")]
