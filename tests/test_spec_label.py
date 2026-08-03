"""A spec is labelled by its display name only when that is the same name.

The builder listed the stored slug, so the JERM profile read as "jerm". Its
display name is the fix -- but a display name inherited from a template goes
stale: a spec renamed to "Test" still carried "ENA", so preferring it outright
would put someone else's name on the card.
"""

from __future__ import annotations

from types import SimpleNamespace

from metaseed_hub.ui.spec_builder_helpers import spec_label


def _record(name: str, display: str | None):
    data = {"spec": {"display_name": display}} if display is not None else {}
    return SimpleNamespace(name=name, spec_data=data)


def test_a_case_variant_display_name_is_used() -> None:
    """jerm -> JERM: the same name, cased the way the profile writes it."""
    assert spec_label(_record("jerm", "JERM")) == "JERM"


def test_an_unrelated_display_name_is_ignored() -> None:
    """A stale inherited name must not replace the one the author chose."""
    assert spec_label(_record("Test", "ENA")) == "Test"


def test_a_missing_display_name_falls_back_to_the_name() -> None:
    assert spec_label(_record("miappe", None)) == "miappe"
    assert spec_label(_record("miappe", "")) == "miappe"


def test_an_unnamed_draft_is_not_blank() -> None:
    assert spec_label(_record("", None)) == "Untitled"
