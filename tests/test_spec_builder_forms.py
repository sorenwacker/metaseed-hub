"""Tests for spec-builder form parsing."""

import pytest

from metaseed_hub.ui.spec_builder.forms import FieldFormData, ValidationRuleFormData


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


def test_rule_form_parses_numeric_bounds() -> None:
    form = ValidationRuleFormData(
        name="bounds", minimum="1.5", maximum="10", min_items="1", max_items="5"
    )
    assert form.get_minimum() == 1.5
    assert form.get_maximum() == 10.0
    assert form.get_min_items() == 1
    assert form.get_max_items() == 5


def test_rule_form_returns_none_for_blank_bounds() -> None:
    form = ValidationRuleFormData(name="bounds")
    assert form.get_minimum() is None
    assert form.get_maximum() is None
    assert form.get_min_items() is None
    assert form.get_max_items() is None


def test_rule_form_raises_clear_error_on_malformed_number() -> None:
    # Rule routes rely on these accessors so malformed input surfaces as a
    # friendly form error instead of a 500.
    with pytest.raises(ValueError, match="Minimum must be a number"):
        ValidationRuleFormData(name="bounds", minimum="abc").get_minimum()
    with pytest.raises(ValueError, match="Maximum must be a number"):
        ValidationRuleFormData(name="bounds", maximum="abc").get_maximum()
    with pytest.raises(ValueError, match="Minimum items must be a whole number"):
        ValidationRuleFormData(name="bounds", min_items="1.5").get_min_items()
    with pytest.raises(ValueError, match="Maximum items must be a whole number"):
        ValidationRuleFormData(name="bounds", max_items="abc").get_max_items()
