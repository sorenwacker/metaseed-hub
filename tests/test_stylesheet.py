"""The stylesheet says each thing once, and the templates use classes that exist.

`hub.css` grew to 7,600 lines with 43 selectors defined twice -- `.sidebar-toggle`
three times with different `top` values, resolved by whichever came last -- and a
whole `.member-card` family from a members panel that no longer existed, while
the live panel's `.member-add` form had no rule at all. Vulture cannot see CSS
or templates, so these two checks are their vulture.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from metaseed_hub.ui.metaseed_ui import METASEED_STATIC_DIR

HUB_UI = Path("src/metaseed_hub/ui")
HUB_CSS = HUB_UI / "static/css/hub.css"
LIBRARY_STATIC = METASEED_STATIC_DIR


def _top_level_blocks(css: str) -> Iterator[tuple[str, str]]:
    """``(selector, body)`` for every rule outside an at-rule, comments removed."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    depth = 0
    buffer = ""
    body_start = 0
    selector = ""
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                selector = " ".join(buffer.split())
                body_start = index + 1
            depth += 1
            buffer = ""
        elif char == "}":
            depth -= 1
            if depth == 0 and not selector.startswith("@"):
                yield selector, text[body_start:index]
            buffer = ""
        elif depth == 0:
            buffer += char


def test_no_selector_is_defined_twice_at_the_top_level() -> None:
    seen: dict[str, int] = {}
    for selector, _body in _top_level_blocks(HUB_CSS.read_text()):
        seen[selector] = seen.get(selector, 0) + 1
    duplicated = sorted(selector for selector, count in seen.items() if count > 1)
    assert not duplicated, f"defined more than once in hub.css: {duplicated}"


def _classes_in_templates() -> set[str]:
    """Literal class names in the hub's templates, Jinja expressions removed."""
    found: set[str] = set()
    for path in (HUB_UI / "templates").rglob("*.html"):
        text = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", " ", path.read_text(), flags=re.S)
        for attribute in re.findall(r'class="([^"]*)"', text):
            # A token ending in "-" is the literal half of a class the template
            # completes from a value (``diff-{{ kind }}``), not a class itself.
            found.update(
                token
                for token in attribute.split()
                if re.fullmatch(r"[\w-]+", token) and not token.endswith("-")
            )
    return found


def _classes_known_to_styles_or_scripts() -> set[str]:
    """Classes with a rule, or named by a script as a hook, in the hub or the library."""
    sources = [HUB_CSS, *(HUB_UI / "static/js").glob("*.js")]
    sources += list(LIBRARY_STATIC.glob("css/*.css")) + list(LIBRARY_STATIC.glob("js/*.js"))
    known: set[str] = set()
    for path in sources:
        if path.name.endswith(".min.js"):
            continue
        text = path.read_text()
        if path.suffix == ".css":
            known.update(
                re.findall(r"\.([A-Za-z_][\w-]*)", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
            )
        else:
            known.update(re.findall(r"""['"]\.?([A-Za-z_][\w-]*)['"]""", text))
    return known


def test_every_template_class_has_a_rule_or_a_script_that_uses_it() -> None:
    unknown = sorted(_classes_in_templates() - _classes_known_to_styles_or_scripts())
    assert not unknown, (
        "classes the templates use that no stylesheet defines and no script "
        f"reads (drop them, or style them): {unknown}"
    )
