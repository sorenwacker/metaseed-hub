"""Tests for helper functions in metaseed_hub.ui.helpers."""

from datetime import date, datetime
from unittest.mock import Mock

from fastapi import Request
from metaseed.ui.state import AppState, TreeNode

from metaseed_hub.ui.helpers import (
    build_entity_form_context,
    build_inline_tables,
    ensure_dataset_facade,
    escape_pattern_hyphen,
    get_or_create_csrf_token,
    get_tree_data_from_nodes,
    humanize_field_name,
    make_json_serializable,
    validate_csrf_token,
)


class TestCSRFToken:
    """Tests for CSRF token functions."""

    @staticmethod
    def _signed_token() -> str:
        """Mint a signed CSRF token as the application would issue it."""
        request = Mock(spec=Request)
        request.cookies = {}
        return get_or_create_csrf_token(request)

    def test_get_or_create_csrf_token_creates_new(self) -> None:
        """Creates a new signed token when none exists in cookie."""
        request = Mock(spec=Request)
        request.cookies = {}

        token = get_or_create_csrf_token(request)

        assert token is not None
        assert "." in token  # carries an HMAC signature segment

    def test_get_or_create_csrf_token_returns_existing(self) -> None:
        """Returns an existing, validly signed token from the cookie."""
        existing_token = self._signed_token()
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": existing_token}

        token = get_or_create_csrf_token(request)

        assert token == existing_token

    def test_get_or_create_csrf_token_ignores_invalid_token(self) -> None:
        """Creates a new token if the cookie token is not validly signed."""
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": "unsigned_value"}

        token = get_or_create_csrf_token(request)

        assert token != "unsigned_value"
        assert "." in token

    def test_validate_csrf_token_success_header(self) -> None:
        """Validates a signed token from the X-CSRF-Token header."""
        valid_token = self._signed_token()
        request = Mock(spec=Request)
        request.cookies = {"metaseed_csrf_token": valid_token}
        request.headers = {"X-CSRF-Token": valid_token}

        assert validate_csrf_token(request) is True

    def test_validate_csrf_token_success_form(self) -> None:
        """Validates a signed token from the form parameter."""
        valid_token = self._signed_token()
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


class TestEnsureDatasetFacade:
    """Tests for ensure_dataset_facade, the single dataset load path."""

    @staticmethod
    def _dataset(data: dict | None) -> Mock:
        dataset = Mock()
        dataset.id = "ds-1"
        dataset.profile = "miappe"
        dataset.version = "1.1"
        dataset.data = data
        dataset.spec_draft_id = None
        dataset.spec_id = None
        return dataset

    async def test_creates_new_state(self) -> None:
        """Creates a state with a facade for an empty dataset."""
        state = await ensure_dataset_facade(self._dataset(None), Mock())

        assert state is not None
        assert state.profile == "miappe"
        assert state.version == "1.1"
        assert state.facade is not None

    async def test_loads_from_data(self) -> None:
        """Loads the stored entity tree into the facade."""
        data = {
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

        state = await ensure_dataset_facade(self._dataset(data), Mock())

        assert state.profile == "miappe"
        assert [r.entity_type for r in state.facade.get_roots()] == ["Investigation"]


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


class TestMakeJsonSerializable:
    """Tests for make_json_serializable function."""

    def test_date_converted_to_iso_string(self) -> None:
        """Converts date objects to ISO format strings."""
        d = date(2024, 6, 8)
        result = make_json_serializable(d)
        assert result == "2024-06-08"

    def test_datetime_converted_to_iso_string(self) -> None:
        """Converts datetime objects to ISO format strings."""
        dt = datetime(2024, 6, 8, 14, 30, 0)
        result = make_json_serializable(dt)
        assert result == "2024-06-08T14:30:00"

    def test_nested_dict_with_dates(self) -> None:
        """Converts dates in nested dictionaries."""
        data = {
            "name": "Test",
            "start_date": date(2024, 1, 1),
            "nested": {
                "end_date": date(2024, 12, 31),
                "value": 42,
            },
        }
        result = make_json_serializable(data)
        assert result["start_date"] == "2024-01-01"
        assert result["nested"]["end_date"] == "2024-12-31"
        assert result["nested"]["value"] == 42
        assert result["name"] == "Test"

    def test_list_with_dates(self) -> None:
        """Converts dates in lists."""
        data = [date(2024, 1, 1), date(2024, 2, 1), "string", 123]
        result = make_json_serializable(data)
        assert result == ["2024-01-01", "2024-02-01", "string", 123]

    def test_mixed_nested_structure(self) -> None:
        """Handles complex nested structures with dates."""
        data = {
            "entities": [
                {
                    "type": "Investigation",
                    "data": {
                        "submission_date": date(2024, 6, 1),
                        "created_at": datetime(2024, 6, 1, 10, 0, 0),
                    },
                },
                {
                    "type": "Study",
                    "dates": [date(2024, 7, 1), date(2024, 8, 1)],
                },
            ],
        }
        result = make_json_serializable(data)
        assert result["entities"][0]["data"]["submission_date"] == "2024-06-01"
        assert result["entities"][0]["data"]["created_at"] == "2024-06-01T10:00:00"
        assert result["entities"][1]["dates"] == ["2024-07-01", "2024-08-01"]

    def test_primitives_unchanged(self) -> None:
        """Leaves primitive types unchanged."""
        assert make_json_serializable("string") == "string"
        assert make_json_serializable(42) == 42
        assert make_json_serializable(3.14) == 3.14
        assert make_json_serializable(True) is True
        assert make_json_serializable(None) is None

    def test_empty_structures(self) -> None:
        """Handles empty dicts and lists."""
        assert make_json_serializable({}) == {}
        assert make_json_serializable([]) == []

    def test_pydantic_url_types(self) -> None:
        """Converts Pydantic URL types to strings."""
        from pydantic import AnyUrl, HttpUrl

        url = AnyUrl("https://example.com/path")
        result = make_json_serializable(url)
        assert result == "https://example.com/path"
        assert isinstance(result, str)

        http_url = HttpUrl("https://example.org/api")
        result = make_json_serializable(http_url)
        assert result == "https://example.org/api"
        assert isinstance(result, str)

    def test_nested_pydantic_urls(self) -> None:
        """Converts Pydantic URLs in nested structures."""
        from pydantic import AnyUrl

        data = {
            "name": "Test",
            "website": AnyUrl("https://example.com"),
            "links": [
                AnyUrl("https://link1.com"),
                AnyUrl("https://link2.com"),
            ],
        }
        result = make_json_serializable(data)
        assert result["website"] == "https://example.com/"
        assert result["links"][0] == "https://link1.com/"
        assert result["links"][1] == "https://link2.com/"

    def test_tuple_converted_to_list(self) -> None:
        """Converts tuples to lists."""
        data = (1, 2, date(2024, 1, 1))
        result = make_json_serializable(data)
        assert result == [1, 2, "2024-01-01"]
        assert isinstance(result, list)

    def test_actual_json_serializable(self) -> None:
        """Result can actually be serialized to JSON."""
        import json

        from pydantic import AnyUrl

        data = {
            "date": date(2024, 6, 8),
            "datetime": datetime(2024, 6, 8, 12, 0),
            "url": AnyUrl("https://example.com"),
            "nested": {
                "urls": [AnyUrl("https://a.com"), AnyUrl("https://b.com")],
            },
        }
        result = make_json_serializable(data)
        # This should not raise
        json_str = json.dumps(result)
        assert "2024-06-08" in json_str
        assert "example.com" in json_str


class TestMetaseedSerializeContract:
    """Tests that verify metaseed returns JSON-serializable data.

    These tests verify metaseed's contract - if they fail, metaseed has a bug.
    metaseed-hub should not work around these issues; metaseed must fix them.
    """

    def test_isa_serialize_is_json_serializable(self) -> None:
        """ISA profile serialize() output must be JSON-serializable."""
        import json

        from metaseed import MetaseedClient

        client = MetaseedClient("isa", "1.0")
        client.create_entity(
            "Investigation",
            {
                "identifier": "test-inv",
                "title": "Test Investigation",
                "submission_date": "2024-01-01",
            },
        )

        tree_data = client.serialize(format="tree")

        # This must not raise - if it does, metaseed has a bug
        json.dumps(tree_data)

    def test_miappe_serialize_is_json_serializable(self) -> None:
        """MIAPPE profile serialize() output must be JSON-serializable."""
        import json

        from metaseed import MetaseedClient

        client = MetaseedClient("miappe", "1.1")
        client.create_entity(
            "Investigation",
            {
                "unique_id": "test-inv",
                "title": "Test Investigation",
                "submission_date": "2024-01-01",
                "public_release_date": "2024-12-31",
            },
        )

        tree_data = client.serialize(format="tree")

        # This must not raise - if it does, metaseed has a bug
        json.dumps(tree_data)


class TestParseWorkbookSheets:
    """Tests for workbook parsing edge cases."""

    def test_empty_row_tuple_is_skipped(self) -> None:
        """A row with no cell records must be skipped, not raise IndexError.

        openpyxl in read_only mode can yield such rows as empty tuples for
        formatted-but-empty rows, which previously 500ed both import routes.
        """
        from unittest.mock import patch

        from metaseed_hub.ui.helpers import parse_workbook_sheets

        class _FakeSheet:
            def iter_rows(self, values_only: bool = True):
                return iter([("title",), ("A",), (), ("B",)])

        class _FakeWorkbook:
            sheetnames = ["Investigation"]

            def __getitem__(self, name: str) -> _FakeSheet:
                return _FakeSheet()

        with patch("openpyxl.load_workbook", return_value=_FakeWorkbook()):
            result = parse_workbook_sheets(b"ignored")

        assert result == {"Investigation": [{"title": "A"}, {"title": "B"}]}


class TestEntityImportHelpers:
    """Tests for the shared import grouping and node-creation helpers."""

    @staticmethod
    def _state() -> AppState:
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        return state

    def test_group_defaults_untyped_payloads(self) -> None:
        from metaseed_hub.ui.helpers import group_entities_by_type

        grouped = group_entities_by_type(
            [{"_type": "Study", "title": "S"}, {"title": "untyped"}], "Investigation"
        )

        assert grouped == {
            "Study": [{"_type": "Study", "title": "S"}],
            "Investigation": [{"title": "untyped"}],
        }

    def test_add_entities_in_order_imports_and_strips_metadata(self) -> None:
        from metaseed_hub.ui.helpers import add_entities_in_order

        state = self._state()
        facade = state.get_or_create_facade()
        entities_by_type = {
            "Study": [{"title": "S", "_type": "Study", "_node_id": "old"}],
            "Investigation": [{"title": "I"}],
        }

        imported, errors = add_entities_in_order(state, facade, entities_by_type, "Investigation")

        assert imported == 2
        assert errors == []
        types = sorted(n.entity_type for n in state.nodes_by_id.values())
        assert types == ["Investigation", "Study"]

    def test_add_entities_reports_unknown_types(self) -> None:
        """A payload typed with a non-entity name is an error, not a silent skip."""
        from metaseed_hub.ui.helpers import add_entities_in_order

        state = self._state()
        facade = state.get_or_create_facade()
        entities_by_type = {"miappe": [{"title": "wrongly typed"}]}

        imported, errors = add_entities_in_order(state, facade, entities_by_type, "Investigation")

        assert imported == 0
        assert len(errors) == 1
        assert "miappe" in errors[0]
