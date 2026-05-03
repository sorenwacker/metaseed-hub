"""Security tests for metaseed_hub.ui.

Tests for:
- CSRF validation on POST/PUT/DELETE endpoints
- Authorization enforcement
- XSS prevention
"""

import secrets
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from metaseed_hub.ui.security import (
    CSRFValidationError,
    csrf_error_response,
    require_csrf,
    validate_csrf_or_error,
)


class TestCSRFValidation:
    """Tests for CSRF validation functions."""

    def test_validate_csrf_or_error_valid(self) -> None:
        """Does not raise when token is valid."""
        valid_token = secrets.token_urlsafe(32)
        request = Mock()
        request.cookies = {"metaseed_csrf_token": valid_token}
        request.headers = {"X-CSRF-Token": valid_token}

        # Should not raise
        validate_csrf_or_error(request)

    def test_validate_csrf_or_error_invalid(self) -> None:
        """Raises CSRFValidationError when token is invalid."""
        request = Mock()
        request.cookies = {"metaseed_csrf_token": "cookie_token"}
        request.headers = {"X-CSRF-Token": "different_token"}

        with pytest.raises(CSRFValidationError):
            validate_csrf_or_error(request)

    def test_validate_csrf_or_error_missing_cookie(self) -> None:
        """Raises CSRFValidationError when cookie is missing."""
        request = Mock()
        request.cookies = {}
        request.headers = {"X-CSRF-Token": "some_token"}

        with pytest.raises(CSRFValidationError):
            validate_csrf_or_error(request)

    def test_validate_csrf_or_error_missing_header(self) -> None:
        """Raises CSRFValidationError when header is missing."""
        request = Mock()
        request.cookies = {"metaseed_csrf_token": "some_token"}
        request.headers = {}

        with pytest.raises(CSRFValidationError):
            validate_csrf_or_error(request)

    def test_csrf_error_response(self) -> None:
        """Returns proper error response."""
        response = csrf_error_response()

        assert response.status_code == 403
        assert b"CSRF validation failed" in response.body


class TestRequireCSRFDecorator:
    """Tests for require_csrf decorator."""

    @pytest.mark.asyncio
    async def test_require_csrf_passes_valid(self) -> None:
        """Decorated function runs when CSRF is valid."""
        from fastapi import Request as FastAPIRequest

        valid_token = secrets.token_urlsafe(32)
        request = Mock(spec=FastAPIRequest)
        request.cookies = {"metaseed_csrf_token": valid_token}
        request.headers = {"X-CSRF-Token": valid_token}

        @require_csrf
        async def handler(request: FastAPIRequest) -> str:
            return "success"

        result = await handler(request=request)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_require_csrf_raises_invalid(self) -> None:
        """Decorated function raises when CSRF is invalid."""
        from fastapi import Request as FastAPIRequest

        request = Mock(spec=FastAPIRequest)
        request.cookies = {}
        request.headers = {}

        @require_csrf
        async def handler(request: FastAPIRequest) -> str:
            return "success"

        with pytest.raises(CSRFValidationError):
            await handler(request=request)

    @pytest.mark.asyncio
    async def test_require_csrf_with_form_token(self) -> None:
        """Decorated function accepts form token parameter."""
        from fastapi import Request as FastAPIRequest

        valid_token = secrets.token_urlsafe(32)
        request = Mock(spec=FastAPIRequest)
        request.cookies = {"metaseed_csrf_token": valid_token}
        request.headers = {}

        @require_csrf
        async def handler(request: FastAPIRequest, csrf_token: str) -> str:
            return "success"

        result = await handler(request=request, csrf_token=valid_token)
        assert result == "success"


class TestAuthorizationDependencies:
    """Tests for authorization dependency functions."""

    @pytest.mark.asyncio
    async def test_verify_workspace_access_not_found(self) -> None:
        """Raises 404 when workspace not found."""
        from metaseed_hub.ui.dependencies import verify_workspace_access

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        user = Mock()
        user.keycloak_id = "user12345678"

        with pytest.raises(HTTPException) as exc_info:
            await verify_workspace_access("nonexistent", session, user)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_verify_workspace_access_denied(self) -> None:
        """Raises 403 when user doesn't own workspace."""
        from metaseed_hub.ui.dependencies import verify_workspace_access

        workspace = Mock()
        workspace.id = "ws-1"
        workspace.tenant_id = "tenant-other"

        session = AsyncMock()
        # First call returns workspace, second call returns None for tenant
        result_mock1 = Mock()
        result_mock1.scalar_one_or_none.return_value = workspace
        result_mock2 = Mock()
        result_mock2.scalar_one_or_none.return_value = None
        session.execute.side_effect = [result_mock1, result_mock2]

        user = Mock()
        user.keycloak_id = "user12345678"

        with pytest.raises(HTTPException) as exc_info:
            await verify_workspace_access("ws-1", session, user)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_project_for_user_not_found(self) -> None:
        """Raises 404 when project not found."""
        from metaseed_hub.ui.dependencies import get_project_for_user

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        user = Mock()
        user.keycloak_id = "user12345678"

        with pytest.raises(HTTPException) as exc_info:
            await get_project_for_user("nonexistent", session, user)

        assert exc_info.value.status_code == 404


class TestXSSPrevention:
    """Tests for XSS prevention in templates and handlers."""

    def test_chat_message_escapes_html(self) -> None:
        """Verifies HTML in chat messages is escaped."""
        import html

        user_input = "<script>alert('xss')</script>"
        escaped = html.escape(user_input)

        assert "&lt;script&gt;" in escaped
        assert "<script>" not in escaped

    def test_project_name_escaped_in_template_context(self) -> None:
        """Project names are escaped by Jinja2 autoescaping."""
        # Jinja2 auto-escapes by default
        # This test verifies our understanding
        from jinja2 import Environment, select_autoescape

        env = Environment(autoescape=select_autoescape())
        template = env.from_string("<h1>{{ project.name }}</h1>")

        class FakeProject:
            name = "<script>alert('xss')</script>"

        result = template.render(project=FakeProject())

        assert "&lt;script&gt;" in result
        assert "<script>" not in result


class TestFormProcessing:
    """Tests for form processing utilities."""

    def test_parse_form_field_string(self) -> None:
        """Parses string field correctly."""
        from metaseed_hub.ui.forms import parse_form_field

        assert parse_form_field("hello", "string") == "hello"

    def test_parse_form_field_integer(self) -> None:
        """Parses integer field correctly."""
        from metaseed_hub.ui.forms import parse_form_field

        assert parse_form_field("42", "integer") == 42

    def test_parse_form_field_float(self) -> None:
        """Parses float field correctly."""
        from metaseed_hub.ui.forms import parse_form_field

        assert parse_form_field("3.14", "float") == 3.14

    def test_parse_form_field_boolean_true(self) -> None:
        """Parses boolean true correctly."""
        from metaseed_hub.ui.forms import parse_form_field

        assert parse_form_field("true", "boolean") is True
        assert parse_form_field("1", "boolean") is True
        assert parse_form_field("yes", "boolean") is True

    def test_parse_form_field_boolean_false(self) -> None:
        """Parses boolean false correctly."""
        from metaseed_hub.ui.forms import parse_form_field

        assert parse_form_field("false", "boolean") is False
        assert parse_form_field("0", "boolean") is False

    def test_parse_form_field_empty(self) -> None:
        """Empty string returns None."""
        from metaseed_hub.ui.forms import parse_form_field

        assert parse_form_field("", "string") is None

    def test_get_label_from_values_title(self) -> None:
        """Extracts label from title field."""
        from metaseed_hub.ui.forms import get_label_from_values

        values = {"title": "My Title", "description": "A description"}
        assert get_label_from_values(values) == "My Title"

    def test_get_label_from_values_name(self) -> None:
        """Extracts label from name field."""
        from metaseed_hub.ui.forms import get_label_from_values

        values = {"name": "Entity Name", "type": "Investigation"}
        assert get_label_from_values(values) == "Entity Name"

    def test_get_label_from_values_person(self) -> None:
        """Extracts label from person name fields."""
        from metaseed_hub.ui.forms import get_label_from_values

        values = {"first_name": "John", "last_name": "Doe"}
        assert get_label_from_values(values) == "John Doe"

    def test_get_label_from_values_none(self) -> None:
        """Returns None when no suitable field found."""
        from metaseed_hub.ui.forms import get_label_from_values

        values = {"field1": "value1", "field2": "value2"}
        assert get_label_from_values(values) is None
