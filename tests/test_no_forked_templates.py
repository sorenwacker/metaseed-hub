"""No hub template may shadow a metaseed template of the same path.

The hub's Jinja loader searches its own template directory before metaseed's, so
a file at the same relative path silently replaces the library's. That is how
``spec_builder/partials/field_form.html`` came to be a fork: it drifted until it
had lost all 31 guidance tooltips the library version carries, and every fix to
the field editor had to be made twice.

Shadowing is sometimes the right answer, but it must be a decision rather than an
accident, so a deliberate override is listed here with its reason.
"""

from __future__ import annotations

from pathlib import Path

from metaseed_hub.ui.app import TEMPLATES_DIR
from metaseed_hub.ui.metaseed_ui import METASEED_TEMPLATES_DIR

# Relative paths the hub intentionally overrides, each with the reason it must.
DELIBERATE_OVERRIDES: dict[str, str] = {}

# Forks that predate this gate. Each is a copy that has to be reconciled with the
# library version and deleted, the way field_form.html was; until then the fix
# for any of them has to be made twice. Listed so the gate can block *new* forks
# without waiting for the backlog to clear. Shrink this list, never grow it.
NOT_YET_UNFORKED: frozenset[str] = frozenset(
    {
        "base.html",
        "explore/index.html",
        "partials/inline_table.html",
        "spec_builder/base.html",
        "spec_builder/partials/entities_list.html",
        "spec_builder/partials/entity_editor.html",
        "spec_builder/partials/profile_metadata_form.html",
        "spec_builder/partials/save_result.html",
        "spec_builder/partials/validation_rule_form.html",
        "spec_builder/partials/validation_rules_list.html",
        "spec_builder/partials/yaml_preview.html",
        "spec_builder/start.html",
    }
)


def _templates(root: Path) -> set[str]:
    """Every template path under ``root``, relative to it."""
    return {str(path.relative_to(root)) for path in root.rglob("*.html") if path.is_file()}


def test_no_hub_template_silently_shadows_a_library_one() -> None:
    hub = _templates(Path(TEMPLATES_DIR))
    library = _templates(Path(METASEED_TEMPLATES_DIR))

    shadowed = sorted((hub & library) - set(DELIBERATE_OVERRIDES) - NOT_YET_UNFORKED)

    assert not shadowed, (
        "these hub templates shadow a metaseed template of the same path, so the "
        "library's version is never rendered and fixes to it never reach the hub: "
        f"{shadowed}. Render the library's template instead, or record the "
        "override in DELIBERATE_OVERRIDES with the reason it is needed."
    )


def test_the_unforking_backlog_only_shrinks() -> None:
    """A path listed as a known fork must still be one.

    Once a fork is removed its entry has to go, or the list slowly stops
    describing anything and a re-introduced fork would pass unnoticed.
    """
    hub = _templates(Path(TEMPLATES_DIR))
    library = _templates(Path(METASEED_TEMPLATES_DIR))

    stale = sorted(NOT_YET_UNFORKED - (hub & library))

    assert not stale, (
        f"these are no longer forked and must be removed from NOT_YET_UNFORKED: {stale}"
    )
