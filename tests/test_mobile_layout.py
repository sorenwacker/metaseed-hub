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


def test_the_icon_svgs_have_explicit_dimensions() -> None:
    """An inline SVG with a viewBox but no width/height attributes fills its
    container on mobile browsers (notably iOS Safari), ignoring the CSS width.
    That is what rendered the overview icons full-width on a phone. Every
    decorative icon must carry width and height attributes."""
    import re

    overview = (TEMPLATES_DIR / "partials" / "overview.html").read_text()
    home = (TEMPLATES_DIR / "overview_home.html").read_text()

    for name, html, cls in [
        ("overview", overview, "overview-card-icon"),
        ("home", home, "setup-step-icon"),
    ]:
        tags = re.findall(rf'<svg class="{cls}"[^>]*>', html)
        assert tags, f"no {cls} svgs found in {name}"
        for tag in tags:
            assert "width=" in tag and "height=" in tag, (
                f"{name}: an icon svg lacks width/height and will fill the "
                f"container on mobile: {tag[:80]}"
            )


def test_the_page_does_not_scroll_sideways() -> None:
    """A single element wider than the viewport shifts everything and makes
    centred content — like the sign-in button — look off-centre."""
    css = CSS.read_text()

    assert "overflow-x: hidden" in css


def test_static_assets_are_cache_busted() -> None:
    """The CSS is served `immutable` for a week on a URL with no version, so a
    deploy's CSS never reached a returning browser until the cache expired —
    the reason the mobile fixes appeared not to work. A version query string on
    each asset URL forces a refetch when the release changes."""
    base = (TEMPLATES_DIR / "base.html").read_text()

    assert "hub.css?v=" in base, "the hub stylesheet must be cache-busted"
    assert "style.css?v=" in base, "the core stylesheet must be cache-busted"


def test_the_card_buttons_do_not_lower_their_text() -> None:
    """padding-top on the CTA pushed the label to the bottom of the button;
    spacing from the card must come from margin, not internal padding."""
    css = CSS.read_text()

    rule = css[css.index(".overview-card-cta {") :]
    rule = rule[: rule.index("}")]
    assert "padding-top" not in rule, "padding-top lowers the button text"
