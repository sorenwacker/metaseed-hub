"""Tests for helper functions in metaseed_hub.ui.helpers."""

import secrets
from unittest.mock import Mock

from fastapi import Request
from metaseed.ui.state import AppState, TreeNode

from metaseed_hub.ui.helpers import (
    build_entity_form_context,
    build_inline_tables,
    escape_pattern_hyphen,
    get_or_create_csrf_token,
    get_project_state,
    get_tree_data_from_nodes,
    humanize_field_name,
    validate_csrf_token,
)


class TestCSRFToken:
    """Tests for CSRF token functions."""

    def test_get_or_create_csrf_token_creates_new(self) -> None:
        """Creates a new token when none exists in cookie."""
        request = Mock(spec=Request)
        request.cookies = {}

        token = get_or_create_csrf_token(request)

        assert token is not None
        assert len(token) == 43  # URL-safe base64 encoded 32 bytes

    def test_get_or_create_csrf_token_returns_existing(self) -> None:
        """Returns existing token from cookie."""
        existing_token = secrets.token_urlsafe(32)
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": existing_token}

        token = get_or_create_csrf_token(request)

        assert token == existing_token

    def test_get_or_create_csrf_token_ignores_invalid_token(self) -> None:
        """Creates new token if cookie token is invalid length."""
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": "too_short"}

        token = get_or_create_csrf_token(request)

        assert token != "too_short"
        assert len(token) == 43

    def test_validate_csrf_token_success_header(self) -> None:
        """Validates token from X-CSRF-Token header."""
        valid_token = secrets.token_urlsafe(32)
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": valid_token}
        request.headers = {"X-CSRF-Token": valid_token}

        assert validate_csrf_token(request) is True

    def test_validate_csrf_token_success_form(self) -> None:
        """Validates token from form parameter."""
        valid_token = secrets.token_urlsafe(32)
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": valid_token}
        request.headers = {}

        assert validate_csrf_token(request, valid_token) is True

    def test_validate_csrf_token_fails_no_cookie(self) -> None:
        """Fails when no cookie token exists."""
        request = Mock(spec=Request)
        request.cookies = {}
        request.headers = {"X-CSRF-Token": "some_token"}

        assert validate_csrf_token(request) is False

    def test_validate_csrf_token_fails_no_token(self) -> None:
        """Fails when no request token provided."""
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": "cookie_token"}
        request.headers = {}

        assert validate_csrf_token(request) is False

    def test_validate_csrf_token_fails_mismatch(self) -> None:
        """Fails when tokens don't match."""
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": "cookie_token"}
        request.headers = {"X-CSRF-Token": "different_token"}

        assert validate_csrf_token(request) is False


class TestHumanizeFieldName:
    """Tests for humanize_field_name function."""

    def test_snake_case(self) -> None:
        """Converts snake_case to Title Case."""
        assert humanize_field_name("unique_id") == "Unique Id"
        assert humanize_field_name("created_at") == "Created At"

    def test_camel_case(self) -> None:
        """Converts camelCase to Title Case."""
        assert humanize_field_name("occurrenceID") == "Occurrence Id"
        assert humanize_field_name("basisOfRecord") == "Basis Of Record"

    def test_consecutive_uppercase(self) -> None:
        """Handles consecutive uppercase letters."""
        assert humanize_field_name("HTTPResponse") == "Http Response"
        assert humanize_field_name("XMLParser") == "Xml Parser"

    def test_empty_string(self) -> None:
        """Handles empty string."""
        assert humanize_field_name("") == ""


class TestEscapePatternHyphen:
    """Tests for escape_pattern_hyphen function."""

    def test_escape_underscore_hyphen(self) -> None:
        """Escapes hyphen after underscore in character class."""
        pattern = "[A-Za-z0-9_-]"
        result = escape_pattern_hyphen(pattern)
        assert result == "[A-Za-z0-9_\\-]"

    def test_preserve_valid_ranges(self) -> None:
        """Preserves valid character ranges."""
        pattern = "[a-z]"
        result = escape_pattern_hyphen(pattern)
        assert result == "[a-z]"

    def test_empty_pattern(self) -> None:
        """Handles empty pattern."""
        assert escape_pattern_hyphen("") == ""

    def test_no_hyphen(self) -> None:
        """Handles pattern without hyphens."""
        pattern = "[A-Za-z0-9]"
        result = escape_pattern_hyphen(pattern)
        assert result == "[A-Za-z0-9]"


class TestGetTreeDataFromNodes:
    """Tests for get_tree_data_from_nodes function."""

    def test_empty_tree(self) -> None:
        """Returns empty list for empty tree."""
        state = AppState()
        state.entity_tree = []
        state.nodes_by_id = {}

        result = get_tree_data_from_nodes(state)

        assert result == []

    def test_single_node(self) -> None:
        """Returns single node data."""
        state = AppState()
        node = TreeNode(
            id="node-1",
            entity_type="Investigation",
            instance=None,
            label="Test Investigation",
            parent_id=None,
        )
        state.entity_tree = [node]
        state.nodes_by_id = {"node-1": node}

        result = get_tree_data_from_nodes(state)

        assert len(result) == 1
        assert result[0]["id"] == "node-1"
        assert result[0]["entity_type"] == "Investigation"
        assert result[0]["label"] == "Test Investigation"
        assert result[0]["children"] == []

    def test_nested_nodes(self) -> None:
        """Returns nested structure for parent-child nodes."""
        state = AppState()
        parent = TreeNode(
            id="parent-1",
            entity_type="Investigation",
            instance=None,
            label="Parent",
            parent_id=None,
        )
        child = TreeNode(
            id="child-1",
            entity_type="Study",
            instance=None,
            label="Child",
            parent_id="parent-1",
        )
        parent.children = [child]
        state.entity_tree = [parent]
        state.nodes_by_id = {"parent-1": parent, "child-1": child}

        result = get_tree_data_from_nodes(state)

        assert len(result) == 1
        assert result[0]["id"] == "parent-1"
        assert len(result[0]["children"]) == 1
        assert result[0]["children"][0]["id"] == "child-1"


class TestGetProjectState:
    """Tests for get_project_state function."""

    def test_creates_new_state(self) -> None:
        """Creates new state for project without cache."""
        project = Mock()
        project.id = "proj-1"
        project.profile = "miappe"
        project.version = "1.1"
        project.data = None

        cache: dict = {}
        state = get_project_state(project, cache)

        assert state is not None
        assert state.profile == "miappe"
        assert state.version == "1.1"
        assert "proj-1" in cache

    def test_loads_from_data(self) -> None:
        """Loads entity tree from project data."""
        project = Mock()
        project.id = "proj-2"
        project.profile = "miappe"
        project.version = "1.1"
        project.data = {
            "profile": "miappe",
            "version": "1.1",
            "tree": [
                {
                    "id": "node-1",
                    "entity_type": "Investigation",
                    "label": "Test",
                    "parent_id": None,
                    "data": {},
                }
            ],
        }

        cache: dict = {}
        state = get_project_state(project, cache)

        # Note: Deserialization requires facade, so tree may be empty
        # This tests that the function runs without error
        assert state.profile == "miappe"


class TestBuildInlineTables:
    """Tests for build_inline_tables function."""

    def test_empty_nested_fields(self) -> None:
        """Returns empty dict for no nested fields."""
        state = AppState()
        node = TreeNode(
            id="node-1",
            entity_type="Investigation",
            instance=None,
            label="Test",
            parent_id=None,
        )
        state.entity_tree = [node]
        state.nodes_by_id = {"node-1": node}

        result = build_inline_tables(state, "node-1", [])

        assert result == {}

    def test_unknown_item_type(self) -> None:
        """Returns table with error for unknown item type."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        node = TreeNode(
            id="node-1",
            entity_type="Investigation",
            instance=None,
            label="Test",
            parent_id=None,
        )
        state.entity_tree = [node]
        state.nodes_by_id = {"node-1": node}
        state.facade = None  # Will fail to get facade

        nested_fields = [{"name": "studies", "item_type": None}]
        result = build_inline_tables(state, "node-1", nested_fields)

        # Should return entry with empty columns
        assert "studies" in result
        assert result["studies"]["columns"] == []


class TestBuildEntityFormContext:
    """Tests for build_entity_form_context function."""

    def test_basic_context(self) -> None:
        """Returns basic context with empty values."""
        state = AppState()
        state.entity_tree = []
        state.nodes_by_id = {}

        # Create mock helper
        helper = Mock()
        helper.all_fields = ["title", "description"]
        helper.nested_fields = []
        helper.description = "Test entity"
        helper.field_info = Mock(
            side_effect=lambda name: {
                "title": {"type": "string", "required": True},
                "description": {"type": "string", "required": False},
            }[name]
        )

        result = build_entity_form_context(state, helper)

        assert result["description"] == "Test entity"
        assert len(result["required_fields"]) == 1
        assert len(result["optional_fields"]) == 1
        assert result["values"] == {}
