"""Entitlements must arrive whichever place the issuer puts them.

The dev Keycloak can put ``eduperson_entitlement`` in the access token; SRAM
does not — only its userinfo endpoint carries the full claims. Reading only the
token made the feature flags a dev-only feature that died on deployment, which
is exactly what the production test caught.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from metaseed_hub.auth import OIDCAuth
from metaseed_hub.config import get_settings

URN = "urn:mace:surf.nl:sram:group:tudelft:metaseed:ena"


@pytest.fixture
def auth() -> OIDCAuth:
    return OIDCAuth(get_settings())


async def test_userinfo_supplies_what_the_token_lacks(auth: OIDCAuth) -> None:
    with (
        patch.object(
            auth,
            "get_oidc_config",
            AsyncMock(return_value={"userinfo_endpoint": "https://idp/userinfo"}),
        ),
        patch(
            "httpx.AsyncClient.get",
            AsyncMock(return_value=_response(200, {"eduperson_entitlement": [URN]})),
        ),
    ):
        assert await auth._userinfo_entitlements("tok") == [URN]


async def test_the_answer_is_cached_per_token(auth: OIDCAuth) -> None:
    fetch = AsyncMock(return_value=_response(200, {"eduperson_entitlement": [URN]}))
    with (
        patch.object(
            auth,
            "get_oidc_config",
            AsyncMock(return_value={"userinfo_endpoint": "https://idp/userinfo"}),
        ),
        patch("httpx.AsyncClient.get", fetch),
    ):
        await auth._userinfo_entitlements("tok")
        await auth._userinfo_entitlements("tok")
    assert fetch.await_count == 1, "userinfo must not be called on every request"


async def test_an_idp_hiccup_is_no_entitlements_not_a_failed_login(auth: OIDCAuth) -> None:
    with (
        patch.object(
            auth,
            "get_oidc_config",
            AsyncMock(return_value={"userinfo_endpoint": "https://idp/userinfo"}),
        ),
        patch("httpx.AsyncClient.get", AsyncMock(side_effect=OSError("down"))),
    ):
        assert await auth._userinfo_entitlements("tok") == []


def _response(status: int, payload: dict):
    class _R:
        status_code = status

        @staticmethod
        def json() -> dict:
            return payload

    return _R()
