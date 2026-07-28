"""The header and overview must work on a phone.

The header nav had no small-screen handling, so on mobile it overflowed with no
way to reach it, and the overview icons stretched to their grid area's full
height — reported as "HUGE icons". These check the structural pieces are in
place; the visual result is confirmed on a device.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path("src/metaseed_hub/ui/templates")
CSS = Path("src/metaseed_hub/ui/static/css/hub.css")


def test_the_header_has_a_menu_toggle() -> None:
    """A phone needs a way to open the nav; the desktop row does not fit."""
    base = (TEMPLATES_DIR / "base.html").read_text()

    assert 'class="nav-toggle"' in base
    assert 'id="nav-toggle"' in base, "the checkbox the label toggles"


def test_the_toggle_reveals_the_nav_without_javascript() -> None:
    """A checkbox hack, so the menu works even if scripts do not load."""
    css = CSS.read_text()

    assert ".nav-toggle-checkbox:checked ~ .hub-nav-global" in css


def test_the_menu_toggle_is_hidden_on_desktop() -> None:
    css = CSS.read_text()

    # Default (desktop) state is display:none, revealed inside the media query.
    toggle_rule = css[css.index(".nav-toggle {") : css.index(".nav-toggle {") + 120]
    assert "display: none" in toggle_rule


def test_there_is_a_mobile_breakpoint_for_the_header() -> None:
    css = CSS.read_text()

    assert "@media (max-width: 768px)" in css
    # The nav becomes a dropdown only inside a breakpoint.
    after_media = css[css.rindex("@media (max-width: 768px)") :]
    assert ".hub-nav-global" in after_media


def test_the_overview_icons_cannot_stretch() -> None:
    """They span a multi-row grid cell; without this they grow to its height."""
    css = CSS.read_text()

    block = css[css.index(".overview-card-icon,") :]
    block = block[: block.index("}")]
    assert "flex: 0 0 auto" in block
    assert "align-self: start" in block
