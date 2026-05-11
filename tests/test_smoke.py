"""Smoke tests for critical UI pages and static assets.

These tests verify that pages load correctly and all referenced static
assets are accessible. They catch issues like broken paths, missing CSS,
and template rendering errors before deployment.

Tests are split into:
- Static asset tests: No database required, always run
- Template validation: Parse templates and verify static refs exist
- Route tests: Require database, marked as integration tests
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from metaseed_hub.ui.app import create_hub_app


@pytest.fixture
def client() -> TestClient:
    """Create test client for the hub app."""
    app = create_hub_app()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_with_mocked_db() -> TestClient:
    """Create test client with mocked database dependency."""
    app = create_hub_app()

    # Mock the database session dependency
    async def mock_get_session():
        mock_session = AsyncMock()
        yield mock_session

    # Override the dependency
    from metaseed_hub.database import get_session

    app.dependency_overrides[get_session] = mock_get_session

    return TestClient(app, raise_server_exceptions=False)


class TestStaticAssets:
    """Verify all static assets are accessible."""

    def test_hub_css_accessible(self, client: TestClient) -> None:
        """Hub CSS file loads."""
        response = client.get("/hub-static/css/hub.css")
        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")

    def test_hub_js_accessible(self, client: TestClient) -> None:
        """Hub JS file loads."""
        response = client.get("/hub-static/js/hub.js")
        assert response.status_code == 200

    def test_metaseed_css_accessible(self, client: TestClient) -> None:
        """Metaseed CSS file loads (used by explorer)."""
        response = client.get("/static/css/style.css")
        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")

    def test_metaseed_erd_js_accessible(self, client: TestClient) -> None:
        """ERD common JS file loads (required by explorer)."""
        response = client.get("/static/js/erd-common.js")
        assert response.status_code == 200

    def test_spec_builder_js_accessible(self, client: TestClient) -> None:
        """Spec builder JS file loads."""
        response = client.get("/hub-static/js/spec-builder.js")
        assert response.status_code == 200


class TestTemplateRendering:
    """Verify templates render without errors."""

    def test_login_page_renders(self, client_with_mocked_db: TestClient) -> None:
        """Login page renders without auth."""
        response = client_with_mocked_db.get("/")
        assert response.status_code == 200
        assert b"html" in response.content.lower()

    def test_explore_redirects_to_login(self, client_with_mocked_db: TestClient) -> None:
        """Explorer redirects unauthenticated users to login."""
        response = client_with_mocked_db.get("/explore/", follow_redirects=False)
        assert response.status_code == 302
        assert "/auth/login" in response.headers.get("location", "")

    def test_spec_builder_redirects_to_login(self, client_with_mocked_db: TestClient) -> None:
        """Spec builder redirects unauthenticated users to login."""
        response = client_with_mocked_db.get("/spec-builder", follow_redirects=False)
        assert response.status_code == 302
        assert "/auth/login" in response.headers.get("location", "")


class TestTemplateStaticReferences:
    """Verify templates reference existing static files."""

    TEMPLATE_DIR = Path(__file__).parent.parent / "src/metaseed_hub/ui/templates"
    STATIC_DIR = Path(__file__).parent.parent / "src/metaseed_hub/ui/static"

    # Patterns to find static file references
    STATIC_PATTERNS = [
        r'src=["\']([^"\']+\.js)',
        r'href=["\']([^"\']+\.css)',
    ]

    def _extract_static_refs(self, content: str) -> list[str]:
        """Extract static file references from template content."""
        refs = []
        for pattern in self.STATIC_PATTERNS:
            refs.extend(re.findall(pattern, content))
        return refs

    def _is_local_static(self, path: str) -> bool:
        """Check if path is a local static reference."""
        return path.startswith("/hub/hub-static/") or path.startswith("/hub/static/")

    def _resolve_static_path(self, path: str, client: TestClient) -> int:
        """Resolve static path and return HTTP status code."""
        # Convert /hub/hub-static/ to /hub-static/
        if path.startswith("/hub/hub-static/"):
            path = path.replace("/hub/hub-static/", "/hub-static/")
        elif path.startswith("/hub/static/"):
            path = path.replace("/hub/static/", "/static/")

        # Strip query params
        path = path.split("?")[0]

        response = client.get(path)
        return response.status_code

    def test_explore_template_static_refs(self, client: TestClient) -> None:
        """All static refs in explore template are accessible."""
        template = self.TEMPLATE_DIR / "explore/index.html"
        content = template.read_text()

        refs = self._extract_static_refs(content)
        local_refs = [r for r in refs if self._is_local_static(r)]

        for ref in local_refs:
            status = self._resolve_static_path(ref, client)
            assert status == 200, f"Static file not found: {ref}"

    def test_base_template_static_refs(self, client: TestClient) -> None:
        """All static refs in base template are accessible."""
        template = self.TEMPLATE_DIR / "base.html"
        content = template.read_text()

        refs = self._extract_static_refs(content)
        local_refs = [r for r in refs if self._is_local_static(r)]

        for ref in local_refs:
            status = self._resolve_static_path(ref, client)
            assert status == 200, f"Static file not found: {ref}"


class TestExploreRoutes:
    """Test explorer route functionality."""

    def test_explore_router_registered(self) -> None:
        """Explore router is registered on the app."""
        app = create_hub_app()
        routes = [r.path for r in app.routes]
        assert "/explore/" in routes or any("/explore" in str(r) for r in routes)

    def test_explore_compare_endpoint_exists(self, client_with_mocked_db: TestClient) -> None:
        """Compare endpoint exists (returns 401 without auth, not 404)."""
        response = client_with_mocked_db.post("/explore/compare")
        # Should be 401 (unauthorized) not 404 (not found)
        assert response.status_code in (401, 422), f"Expected 401/422, got {response.status_code}"


class TestSpecBuilderRoutes:
    """Test spec builder route functionality."""

    def test_spec_builder_router_registered(self) -> None:
        """Spec builder router is registered on the app."""
        app = create_hub_app()
        routes = [str(r.path) for r in app.routes]
        assert any("spec-builder" in r for r in routes)
