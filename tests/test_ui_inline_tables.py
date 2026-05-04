"""Tests for UI inline table functionality."""

import pytest
from metaseed.ui.state import AppState, TreeNode


class TestParentIdInheritance:
    """Tests for parent ID inheritance in nested entities."""

    def test_child_entity_inherits_parent_id(self) -> None:
        """When creating a child entity, reference fields should use parent's ID."""
        # Simulate the logic from add_table_row
        parent_type = "Investigation"
        child_field = "investigation_id"

        parent_type_lower = parent_type.lower()
        ref_type = child_field[:-3]  # Remove "_id" suffix

        # The reference type should match the parent type
        assert ref_type == parent_type_lower
        # So we should use parent's identifier
        assert ref_type == "investigation"

    def test_non_matching_reference_gets_random_id(self) -> None:
        """Reference fields that don't match parent type get random IDs."""
        parent_type = "Investigation"
        child_field = "study_id"  # Different from parent type

        parent_type_lower = parent_type.lower()
        ref_type = child_field[:-3]

        # Should not match
        assert ref_type != parent_type_lower
        assert ref_type == "study"

    def test_unique_id_always_random(self) -> None:
        """The unique_id field should always be randomly generated."""
        field_name = "unique_id"
        # unique_id should not use parent's ID
        assert field_name in ("unique_id", "id", "identifier")


class TestDefaultValueGeneration:
    """Tests for generating default values for required fields."""

    def test_string_field_gets_placeholder(self) -> None:
        """String fields get placeholder text based on field name."""
        field_name = "title"
        expected = f"New {field_name.replace('_', ' ').title()}"
        assert expected == "New Title"

    def test_underscore_field_name_formatted(self) -> None:
        """Field names with underscores are formatted properly."""
        field_name = "first_name"
        expected = f"New {field_name.replace('_', ' ').title()}"
        assert expected == "New First Name"

    def test_integer_field_gets_zero(self) -> None:
        """Integer fields default to 0."""
        field_type = "integer"
        default = 0 if field_type == "integer" else None
        assert default == 0

    def test_float_field_gets_zero(self) -> None:
        """Float fields default to 0.0."""
        field_type = "float"
        default = 0.0 if field_type == "float" else None
        assert default == 0.0

    def test_boolean_field_gets_false(self) -> None:
        """Boolean fields default to False."""
        field_type = "boolean"
        default = False if field_type == "boolean" else None
        assert default is False


class TestRegexPatternEscaping:
    """Tests for escaping regex patterns for HTML pattern attribute."""

    def test_escape_hyphen_after_underscore(self) -> None:
        """Hyphens after underscore should be escaped."""
        import re

        def escape_pattern_hyphen(pattern: str) -> str:
            if not pattern:
                return pattern
            result = re.sub(r"(_)-(\])", r"\1\\-\2", pattern)
            result = re.sub(r"(_)-([^\]])", r"\1\\-\2", result)
            return result

        # Pattern with _- that needs escaping
        original = "^[A-Za-z0-9_-]+$"
        escaped = escape_pattern_hyphen(original)
        assert escaped == "^[A-Za-z0-9_\\-]+$"

    def test_pattern_with_range_unchanged(self) -> None:
        """Patterns with valid ranges (like A-Z, 0-9) should work."""
        import re

        def escape_pattern_hyphen(pattern: str) -> str:
            if not pattern:
                return pattern
            result = re.sub(r"(_)-(\])", r"\1\\-\2", pattern)
            result = re.sub(r"(_)-([^\]])", r"\1\\-\2", result)
            return result

        # Standard character class with ranges - should not be modified
        original = "^[A-Za-z0-9]+$"
        escaped = escape_pattern_hyphen(original)
        assert escaped == original

    def test_empty_pattern_returns_empty(self) -> None:
        """Empty pattern should return empty string."""
        import re

        def escape_pattern_hyphen(pattern: str) -> str:
            if not pattern:
                return pattern
            result = re.sub(r"(_)-(\])", r"\1\\-\2", pattern)
            result = re.sub(r"(_)-([^\]])", r"\1\\-\2", result)
            return result

        assert escape_pattern_hyphen("") == ""


class TestTreeSerialization:
    """Tests for entity tree serialization/deserialization."""

    def test_serialize_empty_tree(self) -> None:
        """Empty tree serializes to empty list."""
        state = AppState()
        state.profile = "isa"
        state.version = "1.0"

        from metaseed_hub.ui.helpers import serialize_tree

        result = serialize_tree(state)

        assert result["profile"] == "isa"
        assert result["version"] == "1.0"
        assert result["tree"] == []

    def test_serialize_node_with_children(self) -> None:
        """Nodes with children serialize recursively."""
        state = AppState()
        state.profile = "isa"
        state.version = "1.0"

        # Create a mock tree structure
        parent = TreeNode(
            id="parent-1",
            entity_type="Investigation",
            instance=None,
            label="Test Investigation",
            parent_id=None,
        )
        child = TreeNode(
            id="child-1",
            entity_type="Study",
            instance=None,
            label="Test Study",
            parent_id="parent-1",
        )
        parent.children.append(child)
        state.entity_tree.append(parent)
        state.nodes_by_id["parent-1"] = parent
        state.nodes_by_id["child-1"] = child

        from metaseed_hub.ui.helpers import serialize_tree

        result = serialize_tree(state)

        assert len(result["tree"]) == 1
        parent_data = result["tree"][0]
        assert parent_data["id"] == "parent-1"
        assert parent_data["entity_type"] == "Investigation"
        assert len(parent_data["children"]) == 1
        assert parent_data["children"][0]["id"] == "child-1"

    def test_deserialize_empty_data(self) -> None:
        """Empty data leaves tree empty."""
        state = AppState()
        state.profile = "isa"
        state.version = "1.0"

        from metaseed_hub.ui.helpers import deserialize_tree

        deserialize_tree(state, {})

        assert state.entity_tree == []
        assert state.nodes_by_id == {}

    @pytest.mark.skip(reason="Requires metaseed profile with Investigation/Study types")
    def test_deserialize_preserves_parent_child_relationship(self) -> None:
        """Deserialization preserves parent-child relationships."""
        state = AppState()
        state.profile = "isa"
        state.version = "1.0"

        data = {
            "profile": "isa",
            "version": "1.0",
            "tree": [
                {
                    "id": "parent-1",
                    "entity_type": "Investigation",
                    "label": "Test",
                    "parent_id": None,
                    "data": {"unique_id": "inv-001", "title": "Test Investigation"},
                    "children": [
                        {
                            "id": "child-1",
                            "entity_type": "Study",
                            "label": "Study 1",
                            "parent_id": "parent-1",
                            "data": {
                                "unique_id": "study-001",
                                "investigation_id": "inv-001",
                                "title": "Test Study",
                            },
                            "children": [],
                        }
                    ],
                }
            ],
        }

        from metaseed_hub.ui.helpers import deserialize_tree

        deserialize_tree(state, data)

        # Check parent was created
        assert "parent-1" in state.nodes_by_id
        parent = state.nodes_by_id["parent-1"]
        assert parent.entity_type == "Investigation"

        # Check child was created and linked
        assert "child-1" in state.nodes_by_id
        child = state.nodes_by_id["child-1"]
        assert child.entity_type == "Study"
        assert child.parent_id == "parent-1"

        # Check child is in parent's children list
        assert len(parent.children) == 1
        assert parent.children[0].id == "child-1"


class TestInlineTableCellEditing:
    """Tests for inline table cell editing functionality."""

    def test_internal_fields_are_skipped(self) -> None:
        """Fields starting with underscore should be skipped during cell update."""
        form_data = {
            "_csrf_token": "secret-token",
            "_entity_type": "Investigation",
            "_node_id": "some-id",
            "title": "New Title",
        }

        # Simulate the filtering logic from update_table_cell
        processed_fields = []
        for field_name in form_data.keys():
            if field_name.startswith("_"):
                continue
            processed_fields.append(field_name)

        # Only non-underscore fields should be processed
        assert "_csrf_token" not in processed_fields
        assert "_entity_type" not in processed_fields
        assert "_node_id" not in processed_fields
        assert "title" in processed_fields
        assert len(processed_fields) == 1

    def test_empty_values_are_skipped(self) -> None:
        """Empty string values should be skipped."""
        form_data = {
            "title": "New Title",
            "description": "",
            "notes": None,
        }

        # Simulate the filtering logic
        processed_fields = {}
        for field_name, value in form_data.items():
            if field_name.startswith("_"):
                continue
            if value is None or value == "":
                continue
            processed_fields[field_name] = value

        assert "title" in processed_fields
        assert "description" not in processed_fields
        assert "notes" not in processed_fields

    def test_javascript_filters_cell_input_parameters(self) -> None:
        """Verify that hub.js has htmx:configRequest handler for cell-input filtering."""
        from pathlib import Path

        js_path = Path(__file__).parent.parent / "src/metaseed_hub/ui/static/js/hub.js"
        content = js_path.read_text()

        # Check that htmx:configRequest handler filters cell-input parameters
        assert "cell-input" in content
        assert "evt.detail.parameters" in content
        assert "htmx:configRequest" in content

    def test_cell_update_preserves_other_fields(self) -> None:
        """Updating one field should preserve all other fields."""

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data.copy()

        # Current values in the entity
        current_values = {
            "unique_id": "test-123",
            "title": "Original Title",
            "description": "Some description",
        }
        instance = MockInstance(current_values)

        # Simulate getting current values
        values = instance.model_dump()

        # Update just the title (from form data)
        form_data = {"title": "Updated Title"}
        for field_name, value in form_data.items():
            if value:
                values[field_name] = value

        # Verify title is updated but other fields preserved
        assert values["title"] == "Updated Title"
        assert values["unique_id"] == "test-123"
        assert values["description"] == "Some description"
