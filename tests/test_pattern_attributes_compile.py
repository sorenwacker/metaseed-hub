"""Every hardcoded `pattern` attribute must compile in a browser.

An HTML `pattern` is compiled with the RegExp `v` flag in current browsers,
where a literal `-` inside a character class must be escaped. `[a-z0-9_-]`
compiles happily in Python and in older browsers and throws in Chrome:

    Pattern attribute value [a-z_][a-z0-9_-]* is not a valid regular
    expression: Invalid character in character class

The throw does not stay put. Reading `scrollHeight` forces the browser to
compute validity, so `autoResizeTextarea` raised and every statement after it in
that handler was skipped — a broken form field taking unrelated behaviour down
with it.

`escape_pattern_hyphen` already handles this for patterns a profile supplies.
The ones written directly into a template had no such protection, which is what
this checks.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui" / "templates"

#: A literal pattern attribute. Jinja-valued ones are skipped -- those come from
#: a profile and go through `escape_pattern_hyphen` -- but a regex quantifier
#: such as `{36}` is not Jinja and must still be checked.
PATTERN_ATTR = re.compile(r'pattern="([^"]+)"')
JINJA = re.compile(r"\{\{|\{%")

CHARACTER_CLASS = re.compile(r"\[(\^?)((?:\\.|[^\\\]])*)\]")


def _has_literal_unescaped_hyphen(pattern: str) -> bool:
    """Whether any character class holds a `-` that is neither escaped nor a range.

    A `-` is a range operator only with an operand either side. First, last, or
    following another literal `-` makes it a literal, and the `v` flag then
    demands it be written `\\-`.
    """
    for _negated, body in CHARACTER_CLASS.findall(pattern):
        # Split into tokens, keeping escapes whole so `\-` is not seen as a `-`.
        tokens = re.findall(r"\\.|.", body)
        for i, token in enumerate(tokens):
            if token != "-":
                continue
            before = tokens[i - 1] if i else None
            after = tokens[i + 1] if i + 1 < len(tokens) else None
            is_range = before is not None and after is not None and before != "-" and after != "-"
            if not is_range:
                return True
    return False


def _hardcoded_patterns() -> list[tuple[str, int, str]]:
    """Every literal `pattern="..."` in a template, excluding Jinja values."""
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            for value in PATTERN_ATTR.findall(line):
                if not JINJA.search(value):
                    found.append((str(path.relative_to(TEMPLATES)), n, value))
    return found


def test_there_are_patterns_to_check() -> None:
    """A gate over an empty set passes for the wrong reason."""
    assert _hardcoded_patterns()


def test_no_pattern_has_an_unescaped_hyphen_in_a_character_class() -> None:
    offenders = [
        f"{path}:{n} pattern={value!r}"
        for path, n, value in _hardcoded_patterns()
        if _has_literal_unescaped_hyphen(value)
    ]

    assert not offenders, (
        "a literal '-' in a character class must be written '\\-', or the "
        "browser refuses the whole pattern under the RegExp 'v' flag and the "
        f"throw breaks unrelated code on the page: {offenders}"
    )
