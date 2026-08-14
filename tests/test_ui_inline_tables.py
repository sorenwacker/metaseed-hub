"""A fresh table row carries only what is genuinely derivable.

`_get_default_values` fabricated placeholder data — "New Title", 0, 0.0,
False — and the add-row route persisted it with validation skipped: values
the user never entered, saved as if they had. Only two things are derivable
for a new row: a fresh identifier (the row needs an identity to be
addressable) and the reference to the parent it was created under.

The previous tests in this file restated the fabrication logic inline
without calling the code; they pinned nothing and are replaced.
"""

from __future__ import annotations

from metaseed.facade import ProfileFacade

from metaseed_hub.ui.routes.table import _get_default_values


def _study_defaults() -> dict:
    facade = ProfileFacade("miappe", "1.1")
    parent = facade.add_entity("Investigation", {"unique_id": "INV-1", "title": "T"})
    return _get_default_values(facade.Study, parent, "INV-1")


def test_no_placeholder_values_are_fabricated() -> None:
    defaults = _study_defaults()
    fabricated = {
        k: v
        for k, v in defaults.items()
        if isinstance(v, str) and v.startswith("New ") or v in (0, 0.0, False)
    }
    assert not fabricated, f"fabricated values would persist as real data: {fabricated}"


def test_the_identifier_and_parent_reference_are_filled() -> None:
    defaults = _study_defaults()
    assert defaults.get("unique_id"), "a new row needs an identity"
    assert defaults.get("investigation_id") == "INV-1"
