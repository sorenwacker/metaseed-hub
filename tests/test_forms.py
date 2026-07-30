"""Tests for form processing utilities."""

from unittest.mock import Mock

import pytest
from starlette.datastructures import FormData

from metaseed_hub.ui.forms import extract_entity_values, parse_form_field


class TestParseFormField:
    """Tests for parse_form_field function."""

    def test_parse_empty_string_returns_none(self) -> None:
        assert parse_form_field("", "string") is None

    def test_parse_invalid_integer_raises_value_error(self) -> None:
        # The table mutation routes rely on this contract: a non-numeric value
        # raises ValueError so the caller can fall back to the raw string instead
        # of 500-ing the request.
        with pytest.raises(ValueError):
            parse_form_field("not-a-number", "integer")
        with pytest.raises(ValueError):
            parse_form_field("not-a-number", "float")

    def test_parse_integer(self) -> None:
        assert parse_form_field("42", "integer") == 42

    def test_parse_float(self) -> None:
        assert parse_form_field("3.14", "float") == 3.14

    def test_parse_boolean_true_values(self) -> None:
        assert parse_form_field("true", "boolean") is True
        assert parse_form_field("True", "boolean") is True
        assert parse_form_field("1", "boolean") is True
        assert parse_form_field("yes", "boolean") is True
        assert parse_form_field("on", "boolean") is True

    def test_parse_boolean_false_values(self) -> None:
        assert parse_form_field("false", "boolean") is False
        assert parse_form_field("no", "boolean") is False
        assert parse_form_field("0", "boolean") is False

    def test_parse_string_unchanged(self) -> None:
        assert parse_form_field("hello world", "string") == "hello world"

    def test_parse_date_as_string(self) -> None:
        assert parse_form_field("2024-01-15", "date") == "2024-01-15"

    def test_parse_uri_as_string(self) -> None:
        assert parse_form_field("https://example.com", "uri") == "https://example.com"


class TestExtractEntityValues:
    """Tests for extract_entity_values function."""

    def _create_helper(self, fields: list[dict]) -> Mock:
        """Create a mock helper with specified fields."""
        helper = Mock()
        helper.all_fields = [f["name"] for f in fields]

        def field_info(name: str) -> dict:
            for f in fields:
                if f["name"] == name:
                    return f
            return {"type": "string"}

        helper.field_info = field_info
        return helper

    def test_extract_basic_string_field(self) -> None:
        """Basic string field extraction from form data."""
        helper = self._create_helper([{"name": "title", "type": "string"}])
        form_data = FormData([("title", "My Title")])

        result = extract_entity_values(form_data, helper)

        assert result == {"title": "My Title"}

    def test_extract_integer_field_converts_type(self) -> None:
        """Integer fields are converted from string to int."""
        helper = self._create_helper([{"name": "count", "type": "integer"}])
        form_data = FormData([("count", "42")])

        result = extract_entity_values(form_data, helper)

        assert result == {"count": 42}

    def test_extract_preserves_existing_simple_values(self) -> None:
        """Existing simple values are preserved when not in form data."""
        helper = self._create_helper(
            [
                {"name": "title", "type": "string"},
                {"name": "description", "type": "string"},
            ]
        )
        form_data = FormData([("title", "New Title")])
        existing = {"title": "Old Title", "description": "Existing description"}

        result = extract_entity_values(form_data, helper, existing)

        assert result["title"] == "New Title"  # Form value overrides
        assert result["description"] == "Existing description"  # Preserved

    def test_extract_preserves_nested_entity_values(self) -> None:
        """Nested entity fields are preserved from existing values.

        This is critical because nested entity fields (like principal_investigator)
        are edited via inline tables, not the main form. When the main form is
        saved, we must not lose the nested entity data.
        """
        helper = self._create_helper(
            [
                {"name": "title", "type": "string"},
                {"name": "principal_investigator", "type": "entity", "items": "Person"},
            ]
        )
        form_data = FormData([("title", "Updated Title")])
        existing = {
            "title": "Old Title",
            "principal_investigator": {"name": "Dr. Smith", "mbox": "smith@example.com"},
        }

        result = extract_entity_values(form_data, helper, existing)

        assert result["title"] == "Updated Title"
        # Nested entity must be preserved!
        assert result["principal_investigator"] == {
            "name": "Dr. Smith",
            "mbox": "smith@example.com",
        }

    def test_extract_preserves_nested_list_values(self) -> None:
        """Nested list fields are preserved from existing values.

        Similar to single entity fields, list fields (like studies under
        an investigation) are edited separately and must not be lost.
        """
        helper = self._create_helper(
            [
                {"name": "title", "type": "string"},
                {"name": "studies", "type": "list", "items": "Study"},
            ]
        )
        form_data = FormData([("title", "Updated Investigation")])
        existing = {
            "title": "Old Title",
            "studies": [{"unique_id": "S1"}, {"unique_id": "S2"}],
        }

        result = extract_entity_values(form_data, helper, existing)

        assert result["title"] == "Updated Investigation"
        # List must be preserved!
        assert result["studies"] == [{"unique_id": "S1"}, {"unique_id": "S2"}]

    def test_empty_string_clears_field(self) -> None:
        """Empty string in form data clears the field value."""
        helper = self._create_helper([{"name": "description", "type": "string"}])
        form_data = FormData([("description", "")])
        existing = {"description": "Old description"}

        result = extract_entity_values(form_data, helper, existing)

        assert "description" not in result  # None values are removed

    def test_empty_string_does_not_clear_id_field(self) -> None:
        """Empty string does not clear inherited _id fields."""
        helper = self._create_helper([{"name": "investigation_id", "type": "string"}])
        form_data = FormData([("investigation_id", "")])
        existing = {"investigation_id": "INV-001"}

        result = extract_entity_values(form_data, helper, existing)

        # _id fields should be preserved even with empty string
        assert result["investigation_id"] == "INV-001"

    def test_invalid_integer_keeps_string(self) -> None:
        """Invalid integer input keeps the original string."""
        helper = self._create_helper([{"name": "count", "type": "integer"}])
        form_data = FormData([("count", "not-a-number")])

        result = extract_entity_values(form_data, helper)

        assert result["count"] == "not-a-number"

    def test_none_values_removed_from_result(self) -> None:
        """None values are filtered out of the result dict."""
        helper = self._create_helper(
            [
                {"name": "title", "type": "string"},
                {"name": "description", "type": "string"},
            ]
        )
        form_data = FormData([("title", "Title"), ("description", "")])

        result = extract_entity_values(form_data, helper)

        assert "title" in result
        assert "description" not in result
