"""Every block in the stylesheet closes.

A merge resolution swallowed the closing brace of one rule, so every rule
after it was nested inside it and ignored by the browser: a label meant to be
hidden was shown, a badge lost its styling, and the publish control lost its
layout and wrapped the toolbar. Nothing failed, because the file still parsed
as far as any test looked. This is that gate.
"""

from __future__ import annotations

from pathlib import Path

from metaseed_hub.ui.metaseed_ui import METASEED_STATIC_DIR

HUB_CSS = Path("src/metaseed_hub/ui/static/css/hub.css")


def _stylesheets() -> list[Path]:
    sheets = [HUB_CSS]
    sheets += sorted(Path(METASEED_STATIC_DIR, "css").glob("*.css"))
    return [s for s in sheets if s.exists()]


def test_every_block_closes() -> None:
    for sheet in _stylesheets():
        text = sheet.read_text()
        assert text.count("{") == text.count("}"), (
            f"{sheet.name}: {text.count('{')} opening braces, {text.count('}')} closing"
        )


def test_no_rule_is_left_nested_inside_another() -> None:
    """Balanced braces are not enough: one rule can close another's block and
    leave the file balanced while the rules in between are still nested."""
    for sheet in _stylesheets():
        depth = 0
        for number, line in enumerate(sheet.read_text().splitlines(), 1):
            opened = line.count("{")
            depth += opened - line.count("}")
            # A selector line at depth 2 is nested. Only @media and @keyframes
            # legitimately nest, and their inner rules sit under an at-rule.
            assert depth <= 2, f"{sheet.name}:{number} nested {depth} deep: {line.strip()}"
