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
    async def test_verify_tenant_access_not_found(self) -> None:
        """Raises 404 when tenant not found."""
        from metaseed_hub.ui.dependencies import verify_tenant_access

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        user = Mock()
        user.keycloak_id = "user12345678"

        with pytest.raises(HTTPException) as exc_info:
            await verify_tenant_access("nonexistent", session, user)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_verify_tenant_access_denied(self) -> None:
        """Raises 403 when user doesn't belong to tenant."""
        from unittest.mock import patch

        from metaseed_hub.ui.dependencies import verify_tenant_access

        requested_tenant = Mock()
        requested_tenant.id = "tenant-1"
        requested_tenant.slug = "other-user"

        user_tenant = Mock()
        user_tenant.id = "tenant-2"  # Different tenant
        user_tenant.slug = "user1234"

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = requested_tenant
        session.execute.return_value = result_mock

        user = Mock()
        user.keycloak_id = "user12345678"

        with patch(
            "metaseed_hub.ui.dependencies.get_tenant_for_user",
            return_value=user_tenant,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await verify_tenant_access("tenant-1", session, user)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_dataset_for_user_not_found(self) -> None:
        """Raises 404 when dataset not found."""
        from metaseed_hub.ui.dependencies import get_dataset_for_user

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        user = Mock()
        user.keycloak_id = "user12345678"

        with pytest.raises(HTTPException) as exc_info:
            await get_dataset_for_user("nonexistent", session, user)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_require_draft_owner_not_found(self) -> None:
        """Raises 404 when the draft does not exist."""
        from metaseed_hub.ui.spec_builder.access import require_draft_owner

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock

        with pytest.raises(HTTPException) as exc_info:
            await require_draft_owner(session, "missing", "user-1")

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_require_draft_owner_denies_non_owner(self) -> None:
        """Raises 403 when the caller does not own the draft."""
        from metaseed_hub.ui.spec_builder.access import require_draft_owner

        draft = Mock()
        draft.user_id = "owner-1"

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = draft
        session.execute.return_value = result_mock

        with pytest.raises(HTTPException) as exc_info:
            await require_draft_owner(session, "draft-1", "intruder-2")

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_draft_owner_allows_owner(self) -> None:
        """Returns the draft when the caller owns it."""
        from metaseed_hub.ui.spec_builder.access import require_draft_owner

        draft = Mock()
        draft.user_id = "owner-1"

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = draft
        session.execute.return_value = result_mock

        assert await require_draft_owner(session, "draft-1", "owner-1") is draft

    @pytest.mark.asyncio
    async def test_require_draft_access_denies_outsider(self) -> None:
        """Raises 403 when the caller can neither own nor access the draft."""
        from unittest.mock import patch

        from metaseed_hub.ui.spec_builder.access import require_draft_access

        draft = Mock()

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = draft
        session.execute.return_value = result_mock

        with patch(
            "metaseed_hub.ui.spec_builder.access._user_can_access_draft",
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await require_draft_access(session, "draft-1", "outsider-2")

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_draft_access_allows_member(self) -> None:
        """Returns the draft when the caller may access it."""
        from unittest.mock import patch

        from metaseed_hub.ui.spec_builder.access import require_draft_access

        draft = Mock()

        session = AsyncMock()
        result_mock = Mock()
        result_mock.scalar_one_or_none.return_value = draft
        session.execute.return_value = result_mock

        with patch(
            "metaseed_hub.ui.spec_builder.access._user_can_access_draft",
            return_value=True,
        ):
            assert await require_draft_access(session, "draft-1", "member-2") is draft


class TestXSSPrevention:
    """Tests for XSS prevention in templates and handlers."""

    def test_chat_message_escapes_html(self) -> None:
        """Verifies HTML in chat messages is escaped."""
        import html

        user_input = "<script>alert('xss')</script>"
        escaped = html.escape(user_input)

        assert "&lt;script&gt;" in escaped
        assert "<script>" not in escaped

    def test_dataset_name_escaped_in_template_context(self) -> None:
        """Dataset names are escaped by Jinja2 autoescaping."""
        # Jinja2 auto-escapes by default
        # This test verifies our understanding
        from jinja2 import Environment, select_autoescape

        env = Environment(autoescape=select_autoescape())
        template = env.from_string("<h1>{{ dataset.name }}</h1>")

        class FakeDataset:
            name = "<script>alert('xss')</script>"

        result = template.render(dataset=FakeDataset())

        assert "&lt;script&gt;" in result
        assert "<script>" not in result

    def test_entity_row_html_escapes_cell_values(self) -> None:
        """User-controlled cell values are escaped when building table-row HTML."""
        from metaseed.ui.state import TreeNode

        from metaseed_hub.ui.routes.table import _build_entity_row_html

        payload = "<script>alert(1)</script>"
        breakout = '"><img src=x onerror=alert(1)>'
        node = TreeNode(
            id="node-1",
            entity_type="Sample",
            instance=None,
            label="Sample 1",
            parent_id="parent-1",
        )

        html = _build_entity_row_html(
            dataset_id="ds-1",
            field_name="samples",
            row_idx=0,
            child_node=node,
            nested_type="Sample",
            columns=["editable_col", "inherited_col"],
            column_types={"editable_col": "string", "inherited_col": "string"},
            inherited_cols={"inherited_col"},
            instance_data={"editable_col": payload, "inherited_col": breakout},
        )

        # No raw tag from the payloads may survive into the markup, and the
        # attribute-breakout quote must be neutralised.
        assert "<script>" not in html
        assert "<img" not in html
        assert '"><img' not in html
        # Their escaped forms should be present instead.
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html


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


class TestOAuthScopes:
    """Tests for OAuth scope configuration."""

    @pytest.mark.asyncio
    async def test_login_uses_configured_scope(self) -> None:
        """Login redirect uses configured oidc_scope from settings."""
        from unittest.mock import AsyncMock, patch

        from metaseed_hub.ui.routes.auth import auth_login

        mock_request = Mock()
        mock_request.cookies = {}

        mock_settings = Mock()
        mock_settings.app_url = "https://example.com"
        mock_settings.effective_client_id = "test-client"
        mock_settings.debug = False
        mock_settings.oidc_scope = "openid email profile offline_access eduperson_entitlement"

        mock_oidc_config = {
            "authorization_endpoint": "https://auth.example.com/authorize",
        }

        with (
            patch(
                "metaseed_hub.ui.routes.auth.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "metaseed_hub.ui.routes.auth.get_oidc_config",
                new_callable=AsyncMock,
                return_value=mock_oidc_config,
            ),
        ):
            response = await auth_login(mock_request)

        # Check that the redirect URL contains the configured scope
        redirect_url = response.headers["location"]
        assert "offline_access" in redirect_url
        assert "eduperson_entitlement" in redirect_url
        assert "scope=" in redirect_url


class TestSpecExportAuth:
    """Tests for spec builder export authentication handling."""

    @pytest.mark.asyncio
    async def test_export_redirects_to_login_when_unauthenticated(self) -> None:
        """Export endpoint redirects to login instead of 401 when not authenticated."""
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from fastapi import APIRouter
        from fastapi.responses import RedirectResponse
        from fastapi.templating import Jinja2Templates

        from metaseed_hub.ui.spec_builder.access import LoginRequiredRedirectError

        mock_request = Mock()
        mock_request.cookies = {}
        mock_session = AsyncMock()

        # Mock get_user_context to raise LoginRequiredRedirectError
        async def mock_get_user_context(*args, **kwargs):
            if kwargs.get("redirect_on_unauthorized"):
                raise LoginRequiredRedirectError()
            return ("user-id", "tenant-id")

        # Patch at the source module since the import happens inside the function
        with patch(
            "metaseed_hub.ui.spec_builder.access.get_user_context",
            side_effect=mock_get_user_context,
        ):
            from metaseed_hub.ui.spec_builder.routes.draft_routes import (
                register_draft_routes,
            )

            # Create a minimal router to get the export function
            router = APIRouter()
            templates = Jinja2Templates(
                directory=str(Path(__file__).parent.parent / "src/metaseed_hub/ui/templates")
            )
            register_draft_routes(router, templates)

            # Find the export route handler
            export_route = None
            for route in router.routes:
                if hasattr(route, "path") and "/export" in route.path:
                    export_route = route
                    break

            assert export_route is not None

            # Call the endpoint directly
            response = await export_route.endpoint(
                request=mock_request,
                draft_id="test-draft-id",
                session=mock_session,
            )

            assert isinstance(response, RedirectResponse)
            assert response.status_code == 302
            assert response.headers["location"] == "/hub/auth/login"

    @pytest.mark.asyncio
    async def test_export_returns_yaml_when_authenticated(self) -> None:
        """Export endpoint returns YAML file when authenticated."""
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from fastapi import APIRouter
        from fastapi.responses import StreamingResponse
        from fastapi.templating import Jinja2Templates
        from metaseed.specs.schema import ProfileSpec

        mock_request = Mock()
        mock_request.cookies = {"metaseed_access_token": "valid-token"}
        mock_session = AsyncMock()

        # Create a mock spec state
        mock_spec = ProfileSpec(name="TestSpec", version="1.0")
        mock_state = Mock()
        mock_state.spec = mock_spec

        mock_draft = Mock()
        mock_draft.id = "test-draft-id"

        # Patch at the source module since the import happens inside the function
        with (
            patch(
                "metaseed_hub.ui.spec_builder.access.get_user_context",
                new_callable=AsyncMock,
                return_value=("user-id", "tenant-id"),
            ),
            patch(
                "metaseed_hub.ui.spec_builder.access.load_state_for_draft",
                new_callable=AsyncMock,
                return_value=(mock_state, mock_draft),
            ),
        ):
            from metaseed_hub.ui.spec_builder.routes.draft_routes import (
                register_draft_routes,
            )

            router = APIRouter()
            templates = Jinja2Templates(
                directory=str(Path(__file__).parent.parent / "src/metaseed_hub/ui/templates")
            )
            register_draft_routes(router, templates)

            # Find the export route handler
            export_route = None
            for route in router.routes:
                if hasattr(route, "path") and "/export" in route.path:
                    export_route = route
                    break

            assert export_route is not None

            response = await export_route.endpoint(
                request=mock_request,
                draft_id="test-draft-id",
                session=mock_session,
            )

            assert isinstance(response, StreamingResponse)
            assert response.media_type == "application/x-yaml"
            assert 'attachment; filename="TestSpec.yaml"' in response.headers["content-disposition"]
