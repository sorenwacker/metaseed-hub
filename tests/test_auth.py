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


def test_standalone_verify_token_accepts_no_settings() -> None:
    """The standalone verify_token takes only the token.

    A settings parameter would be silently ignored after singleton
    initialization, so the signature must not offer one.
    """
    import inspect

    params = list(inspect.signature(auth_module.verify_token).parameters)

    assert params == ["token"]


class TestAllowedSigningAlgorithms:
    """The IdP's discovery document must not choose our verification algorithms.

    `id_token_signing_alg_values_supported` is attacker-adjacent input: a
    compromised or misconfigured IdP advertising `HS256` would make PyJWT
    verify tokens *symmetrically* against the public RSA key — a public value —
    so anyone could mint valid tokens. The allowed list is ours, asymmetric
    only, whatever the discovery document says.
    """

    def test_the_allowed_list_is_fixed_and_asymmetric(self) -> None:
        from metaseed_hub.auth import ALLOWED_SIGNING_ALGORITHMS

        assert set(ALLOWED_SIGNING_ALGORITHMS) == {
            "RS256",
            "RS384",
            "RS512",
            "ES256",
            "ES384",
            "ES512",
            "PS256",
            "PS384",
            "PS512",
        }
        assert not any(a.startswith("HS") for a in ALLOWED_SIGNING_ALGORITHMS)

    def test_verification_does_not_read_algorithms_from_discovery(self) -> None:
        """The decode call must pass our constant, not the discovery list."""
        import inspect

        from metaseed_hub import auth as auth_module

        source = inspect.getsource(auth_module.OIDCAuth.verify_token)
        assert "ALLOWED_SIGNING_ALGORITHMS" in source
        assert "id_token_signing_alg_values_supported" not in source
