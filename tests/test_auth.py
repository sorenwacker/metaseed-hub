"""Tests for OIDC auth caching and key-rotation handling."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import metaseed_hub.auth as auth_module
from metaseed_hub.auth import OIDCAuth, TokenUser


def _auth() -> OIDCAuth:
    return OIDCAuth(settings=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_signing_key_refreshes_jwks_on_kid_miss() -> None:
    """A kid miss invalidates the cached JWKS and refetches once."""
    auth = _auth()
    stale = {"keys": [{"kid": "old"}]}
    fresh = {"keys": [{"kid": "new"}]}
    auth.get_jwks = AsyncMock(side_effect=[stale, fresh])  # type: ignore[method-assign]

    key = await auth._get_signing_key("new")

    assert key == {"kid": "new"}
    assert auth.get_jwks.call_count == 2


@pytest.mark.asyncio
async def test_signing_key_raises_after_refresh_still_missing() -> None:
    """If the key is absent even after a refresh, a 401 is raised."""
    auth = _auth()
    empty = {"keys": []}
    auth.get_jwks = AsyncMock(side_effect=[empty, empty])  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc_info:
        await auth._get_signing_key("whatever")

    assert exc_info.value.status_code == 401
    assert auth.get_jwks.call_count == 2


@pytest.mark.asyncio
async def test_signing_key_found_first_try_does_not_refetch() -> None:
    """When the key is present, the JWKS is fetched only once."""
    auth = _auth()
    auth.get_jwks = AsyncMock(return_value={"keys": [{"kid": "k1"}]})  # type: ignore[method-assign]

    key = await auth._get_signing_key("k1")

    assert key == {"kid": "k1"}
    assert auth.get_jwks.call_count == 1


@pytest.mark.asyncio
async def test_standalone_verify_token_reuses_singleton(monkeypatch) -> None:
    """The standalone verify_token routes through the shared OIDCAuth singleton."""
    fake_auth = AsyncMock()
    fake_auth.verify_token.return_value = TokenUser(sub="s", email="e", name="n", roles=[])
    calls: list[object] = []

    def fake_get_oidc_auth(settings):  # type: ignore[no-untyped-def]
        calls.append(settings)
        return fake_auth

    monkeypatch.setattr(auth_module, "get_oidc_auth", fake_get_oidc_auth)
    monkeypatch.setattr(auth_module, "get_settings", lambda: "settings-obj")

    result = await auth_module.verify_token("a-token")

    assert result.sub == "s"
    fake_auth.verify_token.assert_awaited_once_with("a-token")
    assert calls == ["settings-obj"]
