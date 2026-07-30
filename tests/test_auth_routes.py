"""Tests for OIDC route error handling in metaseed_hub.ui.routes.auth.

Covers the failure modes a user can hit mid sign-in: an unreachable or slow
OIDC provider during discovery must surface as the documented 503, and a
transport failure during the token exchange must produce the same friendly
redirect as any other exchange failure instead of an unhandled 500.
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import metaseed_hub.ui.routes.auth as auth_routes


@pytest.fixture(autouse=True)
def _reset_oidc_config_cache() -> Generator[None, None, None]:
    """Each test starts and ends with an empty OIDC discovery cache."""
    auth_routes._oidc_config = None
    yield
    auth_routes._oidc_config = None


class _FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=Mock(), response=Mock(status_code=self.status_code)
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def _fake_async_client(
    handler: Any,
) -> tuple[type, dict[str, Any]]:
    """Build an httpx.AsyncClient replacement delegating get/post to ``handler``.

    Returns:
        The client class and a dict recording the keyword arguments of the last
        request.
    """
    recorded: dict[str, Any] = {}

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> Any:
            recorded.update(kwargs)
            return handler(url)

        async def post(self, url: str, **kwargs: Any) -> Any:
            recorded.update(kwargs)
            return handler(url)

    return _Client, recorded


class TestGetOIDCConfigErrors:
    """get_oidc_config maps every httpx failure to the documented 503."""

    @pytest.mark.asyncio
    async def test_timeout_maps_to_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hanging provider produces a 503, not an unhandled ReadTimeout."""

        def _raise(_url: str) -> Any:
            raise httpx.ReadTimeout("provider too slow")

        client_cls, _ = _fake_async_client(_raise)
        monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client_cls)

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_oidc_config()

        assert exc_info.value.status_code == 503
        assert "not reachable" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_connect_error_maps_to_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A refused connection produces a 503."""

        def _raise(_url: str) -> Any:
            raise httpx.ConnectError("connection refused")

        client_cls, _ = _fake_async_client(_raise)
        monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client_cls)

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_oidc_config()

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_http_error_status_maps_to_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-2xx discovery response produces a 503 naming the status."""
        client_cls, _ = _fake_async_client(lambda _url: _FakeResponse({}, status_code=500))
        monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client_cls)

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.get_oidc_config()

        assert exc_info.value.status_code == 503
        assert "500" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_discovery_request_sets_explicit_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The discovery request carries an explicit timeout."""
        client_cls, recorded = _fake_async_client(
            lambda _url: _FakeResponse({"issuer": "https://idp.example.org"})
        )
        monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client_cls)

        config = await auth_routes.get_oidc_config()

        assert config == {"issuer": "https://idp.example.org"}
        assert recorded["timeout"] == 10.0


class TestAuthCallbackTokenExchange:
    """auth_callback turns token-exchange failures into user-facing redirects."""

    @staticmethod
    def _request_with_state(state: str) -> Mock:
        request = Mock()
        request.cookies = {auth_routes.STATE_COOKIE: state}
        return request

    @staticmethod
    def _settings() -> Mock:
        settings = Mock()
        settings.app_url = "https://hub.example.org"
        settings.effective_client_id = "hub"
        settings.effective_client_secret = "secret"
        settings.debug = False
        return settings

    @pytest.mark.asyncio
    async def test_transport_error_redirects_instead_of_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An IdP blip during the exchange redirects with token_exchange_failed."""

        def _raise(_url: str) -> Any:
            raise httpx.ConnectError("idp down")

        client_cls, _ = _fake_async_client(_raise)
        monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client_cls)

        with (
            patch.object(auth_routes, "get_settings", return_value=self._settings()),
            patch.object(
                auth_routes,
                "get_oidc_config",
                new=AsyncMock(return_value={"token_endpoint": "https://idp.example.org/token"}),
            ),
        ):
            response = await auth_routes.auth_callback(
                self._request_with_state("state-1"), code="the-code", state="state-1"
            )

        assert isinstance(response, RedirectResponse)
        assert response.status_code == 302
        assert response.headers["location"] == "/hub/?error=token_exchange_failed"

    @pytest.mark.asyncio
    async def test_timeout_error_redirects_instead_of_500(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A token-endpoint timeout redirects with token_exchange_failed."""

        def _raise(_url: str) -> Any:
            raise httpx.ReadTimeout("idp too slow")

        client_cls, _ = _fake_async_client(_raise)
        monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client_cls)

        with (
            patch.object(auth_routes, "get_settings", return_value=self._settings()),
            patch.object(
                auth_routes,
                "get_oidc_config",
                new=AsyncMock(return_value={"token_endpoint": "https://idp.example.org/token"}),
            ),
        ):
            response = await auth_routes.auth_callback(
                self._request_with_state("state-1"), code="the-code", state="state-1"
            )

        assert isinstance(response, RedirectResponse)
        assert response.headers["location"] == "/hub/?error=token_exchange_failed"

    @pytest.mark.asyncio
    async def test_token_exchange_sets_explicit_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The token-exchange POST carries an explicit timeout."""
        client_cls, recorded = _fake_async_client(
            lambda _url: _FakeResponse({"error": "invalid_grant"}, status_code=400)
        )
        monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client_cls)

        with (
            patch.object(auth_routes, "get_settings", return_value=self._settings()),
            patch.object(
                auth_routes,
                "get_oidc_config",
                new=AsyncMock(return_value={"token_endpoint": "https://idp.example.org/token"}),
            ),
        ):
            response = await auth_routes.auth_callback(
                self._request_with_state("state-1"), code="the-code", state="state-1"
            )

        assert recorded["timeout"] == 10.0
        assert response.headers["location"] == "/hub/?error=token_exchange_failed"


def test_access_token_cookie_is_single_sourced() -> None:
    """The cookie name used by writers is the one defined in ui.dependencies.

    Cookie reads (dependencies) and writes (routes.auth, app middleware) must
    share one constant so they cannot silently diverge.
    """
    from metaseed_hub.ui import dependencies

    assert auth_routes.ACCESS_TOKEN_COOKIE is dependencies.ACCESS_TOKEN_COOKIE
