"""OIDC discovery is fetched, cached and error-mapped in one place.

``metaseed_hub.auth.OIDCAuth.get_oidc_config`` is the implementation. The
route module kept a second one -- its own module-global cache, its own httpx
call, its own 503 messages -- so two caches of one document existed with
slightly different behaviour, and tests had to reset a module global. The
routes now read discovery through ``get_oidc_auth``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

import metaseed_hub.auth as auth_module
from metaseed_hub.auth import OIDCAuth
from metaseed_hub.config import get_settings

ROUTES = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui" / "routes" / "auth.py"


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=None,
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def _fake_async_client(get: Any) -> tuple[type, dict[str, Any]]:
    """An ``httpx.AsyncClient`` stand-in whose ``get`` runs ``get`` and records kwargs."""
    recorded: dict[str, Any] = {}

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> Any:
            recorded.update(kwargs)
            return get(url)

    return _Client, recorded


@pytest.mark.asyncio
async def test_discovery_carries_an_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route copy set 10 s; the class copy set none, so a hanging
    provider held every request that needed discovery for as long as httpx's
    default allowed."""
    client_cls, recorded = _fake_async_client(lambda _url: _FakeResponse({"issuer": "x"}))
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", client_cls)

    assert await OIDCAuth(get_settings()).get_oidc_config() == {"issuer": "x"}
    assert recorded.get("timeout") == 10.0


@pytest.mark.asyncio
async def test_a_status_error_names_the_status_and_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client_cls, _ = _fake_async_client(lambda _url: _FakeResponse({}, status_code=500))
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", client_cls)

    with pytest.raises(HTTPException) as refused:
        await OIDCAuth(get_settings()).get_oidc_config()
    assert refused.value.status_code == 503
    assert (
        "500" in refused.value.detail and get_settings().oidc_discovery_url in refused.value.detail
    )


@pytest.mark.asyncio
async def test_an_unreachable_provider_is_a_503_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _raise(_url: str) -> Any:
        raise httpx.ConnectError("refused")

    client_cls, _ = _fake_async_client(_raise)
    monkeypatch.setattr(auth_module.httpx, "AsyncClient", client_cls)

    with pytest.raises(HTTPException) as refused:
        await OIDCAuth(get_settings()).get_oidc_config()
    assert refused.value.status_code == 503
    assert "not reachable" in refused.value.detail


def test_the_route_module_does_not_fetch_discovery_itself() -> None:
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    globals_ = {
        t.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign | ast.Assign)
        for t in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if isinstance(t, ast.Name)
    }
    assert "_oidc_config" not in globals_, "a second discovery cache lives in the route module"
    source = ROUTES.read_text(encoding="utf-8")
    assert "oidc_discovery_url" not in source, "the route module fetches discovery itself"
