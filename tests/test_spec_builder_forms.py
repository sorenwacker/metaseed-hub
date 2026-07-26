"""Tests for spec-builder field form parsing."""

import pytest

from metaseed_hub.ui.spec_builder.forms import FieldFormData


def test_get_constraints_parses_numeric_values() -> None:
    form = FieldFormData(name="age", field_type="integer", min_items="1", max_items="5")
    constraints = form.get_constraints()
    assert constraints is not None
    assert constraints.min_items == 1
    assert constraints.max_items == 5


def test_get_constraints_returns_none_when_empty() -> None:
    assert FieldFormData(name="title", field_type="string").get_constraints() is None


def test_get_constraints_raises_clear_error_on_malformed_number() -> None:
    # A non-numeric constraint value must produce a friendly message, not a
    # bare int()/float() ValueError that surfaces as a 500.
    form = FieldFormData(name="age", field_type="integer", min_length="abc")
    with pytest.raises(ValueError, match="Minimum length must be a whole number"):
        form.get_constraints()
