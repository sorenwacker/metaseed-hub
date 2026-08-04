"""A spec is labelled by its display name, not the slug it is stored under.

The builder and the explorer listed the stored slug, so the JERM profile read as
"jerm" and a specification titled "ACDC Omics Metadata Architecture" appeared as
"acdc_metadata_architecture". A display name inherited from a template can go
stale, which is a defect in the rename path rather than a reason to show an
identifier where a title belongs.
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


def test_the_display_name_wins_even_when_it_differs() -> None:
    """The slug is an identifier; a specification titled by it looks broken."""
    assert (
        spec_label(_record("acdc_metadata_architecture", "ACDC Omics Metadata Architecture"))
        == "ACDC Omics Metadata Architecture"
    )


def test_a_missing_display_name_falls_back_to_the_name() -> None:
    assert spec_label(_record("miappe", None)) == "miappe"
    assert spec_label(_record("miappe", "")) == "miappe"


def test_an_unnamed_draft_is_not_blank() -> None:
    assert spec_label(_record("", None)) == "Untitled"
