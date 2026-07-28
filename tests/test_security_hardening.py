"""Tests for the security-review hardening changes.

Each test encodes a security property and is written to fail against the
pre-hardening code (truncated tenant slug, unbounded upload read, open ontology
proxy, unhandled duplicate-name insert).
"""

import io

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from metaseed_hub.models import Dataset, Tenant
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import MAX_UPLOAD_BYTES, read_upload_capped


class TestTenantSlug:
    """M1: the tenant boundary must derive from the full OIDC subject."""

    def test_deterministic(self) -> None:
        assert tenant_slug_for("subject-abc") == tenant_slug_for("subject-abc")

    def test_uses_full_subject_not_an_8_char_prefix(self) -> None:
        # Two subjects identical in their first 8 characters. The old code keyed
        # the tenant on ``keycloak_id[:8]`` and would map both to one tenant.
        a = "12345678" + "a" * 28
        b = "12345678" + "b" * 28
        assert a[:8] == b[:8]
        assert tenant_slug_for(a) != tenant_slug_for(b)

    def test_slug_is_128_bit_hex(self) -> None:
        slug = tenant_slug_for("any-subject")
        assert len(slug) == 32
        assert all(c in "0123456789abcdef" for c in slug)


class TestUploadCap:
    """L3: an import upload must be bounded rather than read whole into memory."""

    async def test_accepts_content_within_limit(self) -> None:
        payload = b"name: ok\n"
        upload = UploadFile(filename="d.yaml", file=io.BytesIO(payload))
        assert await read_upload_capped(upload, max_bytes=1024) == payload

    async def test_rejects_content_over_limit(self) -> None:
        upload = UploadFile(filename="big.yaml", file=io.BytesIO(b"x" * 2048))
        with pytest.raises(HTTPException) as exc:
            await read_upload_capped(upload, max_bytes=1024)
        assert exc.value.status_code == 413

    def test_default_limit_is_finite(self) -> None:
        assert 0 < MAX_UPLOAD_BYTES <= 100 * 1024 * 1024


class TestOntologyAuth:
    """Informational: the OLS proxy endpoints must require a session."""

    def test_search_requires_authentication(self) -> None:
        from metaseed_hub.ui.routes.ontology_api import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.get("/api/ontology/search", params={"q": "drought"})
        assert resp.status_code == 401


class TestOriginGuard:
    """S3: spec-builder mutations get an Origin-based CSRF defense."""

    @staticmethod
    def _app() -> FastAPI:
        from fastapi import Depends

        from metaseed_hub.ui.security import require_same_origin

        app = FastAPI()

        @app.post("/x", dependencies=[Depends(require_same_origin)])
        def _post() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/y", dependencies=[Depends(require_same_origin)])
        def _get() -> dict[str, bool]:
            return {"ok": True}

        return app

    def test_cross_origin_post_is_blocked(self) -> None:
        client = TestClient(self._app(), base_url="http://testserver")
        resp = client.post("/x", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403

    def test_same_origin_post_is_allowed(self) -> None:
        client = TestClient(self._app(), base_url="http://testserver")
        resp = client.post("/x", headers={"Origin": "http://testserver"})
        assert resp.status_code == 200

    def test_missing_origin_falls_back_to_samesite(self) -> None:
        client = TestClient(self._app(), base_url="http://testserver")
        assert client.post("/x").status_code == 200

    def test_safe_method_is_never_blocked(self) -> None:
        client = TestClient(self._app(), base_url="http://testserver")
        resp = client.get("/y", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 200


class TestOriginGuardIsAppWide:
    """#1: the Origin guard is wired on the whole hub app, not one router."""

    def test_cross_origin_post_to_a_hub_route_is_blocked(self) -> None:
        from metaseed_hub.ui.app import create_hub_app

        client = TestClient(create_hub_app(), raise_server_exceptions=False)
        # A cross-origin POST to the dataset router is rejected by the app-level
        # guard before auth or the route body runs.
        resp = client.post("/datasets", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403
        assert "Cross-origin" in resp.text

    def test_same_origin_post_is_not_blocked_by_the_guard(self) -> None:
        from metaseed_hub.ui.app import create_hub_app

        client = TestClient(create_hub_app(), raise_server_exceptions=False)
        resp = client.post("/datasets", headers={"Origin": "http://testserver"})
        # Same-origin passes the guard; it then fails auth/CSRF, so whatever the
        # status, it must not be the guard's cross-origin rejection.
        assert "Cross-origin" not in resp.text


class TestResetIsPost:
    """S1: the draft reset must not be a state-changing GET."""

    def test_reset_route_is_post_only(self) -> None:
        from fastapi.templating import Jinja2Templates

        from metaseed_hub.ui.spec_builder.routes import create_spec_builder_router

        router = create_spec_builder_router(
            Jinja2Templates(directory="src/metaseed_hub/ui/templates")
        )
        reset = [r for r in router.routes if getattr(r, "path", "").endswith("/reset")]
        assert reset, "reset route not found"
        methods = set().union(*(r.methods for r in reset))
        assert "POST" in methods
        assert "GET" not in methods


class TestCsrfCookieSecure:
    """L1: the CSRF cookie is Secure in a non-debug deployment."""

    def test_csrf_cookie_is_secure_when_not_debug(self, monkeypatch) -> None:
        import metaseed_hub.ui.helpers.csrf as csrf_mod
        from metaseed_hub.config import Settings
        from metaseed_hub.ui.app import create_hub_app

        # Patched where the cookie is actually issued: set_csrf_cookie is the
        # single place that decides the Secure flag, shared by every renderer.
        monkeypatch.setattr(
            csrf_mod, "get_settings", lambda: Settings(debug=False, secret_key="x" * 40)
        )
        client = TestClient(create_hub_app(), raise_server_exceptions=False)
        resp = client.get("/privacy")
        csrf_cookies = [
            c for c in resp.headers.get_list("set-cookie") if "metaseed_csrf_token" in c
        ]
        assert csrf_cookies, "no CSRF cookie was set"
        assert all("Secure" in c for c in csrf_cookies)


class TestDuplicateDatasetName:
    """L4: a tenant cannot hold two datasets with the same name."""

    async def test_duplicate_name_violates_unique_constraint(self, session) -> None:
        tenant = Tenant(name="Acme", slug="s" * 32)
        session.add(tenant)
        await session.flush()

        first = Dataset(tenant_id=tenant.id, name="dup", profile="miappe", version="1.1", data={})
        session.add(first)
        await session.commit()

        second = Dataset(tenant_id=tenant.id, name="dup", profile="miappe", version="1.1", data={})
        session.add(second)
        with pytest.raises(IntegrityError):
            await session.commit()
