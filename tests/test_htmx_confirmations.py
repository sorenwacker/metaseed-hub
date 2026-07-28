"""An htmx control must guard itself with hx-confirm, not onclick.

Reported: clicking Publish and then choosing Cancel published anyway. The button
carried ``onclick="return confirm(...)"``, which cancels a native form submit or
link — but htmx issues its request from its own event listener and ignores the
handler's return value. So the dialog was decorative: every Cancel published.

That is a whole class of bug rather than one button, and publishing now shares a
specification with every user of the hub, so it scans the templates instead of
pinning the single case.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path("src/metaseed_hub/ui/templates")

# Attributes that make htmx issue a request when the element is activated.
HTMX_REQUEST = ("hx-post", "hx-put", "hx-patch", "hx-delete", "hx-get")

# One HTML tag, including attributes across line breaks.
TAG = re.compile(r"<(?:button|a|form|input)\b[^>]*>", re.DOTALL | re.IGNORECASE)


def _tags_with_htmx_and_onclick_confirm() -> list[tuple[Path, str]]:
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for tag in TAG.findall(path.read_text()):
            if not any(attr in tag for attr in HTMX_REQUEST):
                continue
            if "onclick" in tag and "confirm(" in tag:
                offenders.append((path, " ".join(tag.split())[:160]))
    return offenders


def test_no_htmx_control_relies_on_onclick_confirm() -> None:
    """htmx ignores onclick's return value, so the dialog would do nothing."""
    offenders = _tags_with_htmx_and_onclick_confirm()

    assert not offenders, "use hx-confirm instead:\n" + "\n".join(
        f"  {path}: {tag}" for path, tag in offenders
    )


def test_publishing_is_still_confirmed() -> None:
    """Removing the broken guard must not leave publishing unguarded — it now
    shares the specification with every user."""
    base = (TEMPLATES / "spec_builder" / "base.html").read_text()

    publish = next(t for t in TAG.findall(base) if "/publish" in t)
    assert "hx-confirm" in publish
    assert "EVERY user" in publish
