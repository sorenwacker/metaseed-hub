"""The REST API accepts a SRAM token or an API key, and nothing else.

A browser presents an OIDC access token from the SRAM sign-in flow. A script or
an agent cannot obtain one -- getting it requires the interactive flow -- so
without API keys the REST API is reachable only from a browser session. That is
why pushing a dataset from the ``metaseed`` library had no usable credential.

The failure that matters is an API key being treated as more than it is: it acts
for one user's own data and must never carry the admin role an OIDC token can.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.tokens import issue_token, revoke_token
from tests.factories import make_tenant, make_user


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _oidc_returning(user: TokenUser) -> Mock:
    auth = Mock()
    auth.verify_token = AsyncMock(return_value=user)
    return auth


async def _user_with_key(session: AsyncSession, *, slug: str, email: str, **kw):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=email)
    session.add(user)
    await session.commit()
    secret, token = await issue_token(session, user, name="cli", **kw)
    return user, secret, token


async def test_an_api_key_authenticates_as_its_owner(session: AsyncSession) -> None:
    user, secret, _token = await _user_with_key(session, slug="apikey01", email="cli@example.org")

    resolved = await get_current_user(
        credentials=_credentials(secret),
        auth=_oidc_returning(Mock()),
        session=session,
    )

    assert resolved.email == "cli@example.org"
    assert resolved.keycloak_id == user.keycloak_id


async def test_an_api_key_never_carries_the_admin_role(session: AsyncSession) -> None:
    """An API key acts for its user's own data. Granting the admin role through
    one would let a leaked key reach every workspace."""
    _user, secret, _token = await _user_with_key(
        session, slug="apikey02", email="nonadmin@example.org"
    )

    resolved = await get_current_user(
        credentials=_credentials(secret),
        auth=_oidc_returning(Mock()),
        session=session,
    )

    assert resolved.roles == []


async def test_a_sram_token_still_works(session: AsyncSession) -> None:
    """The browser path must be untouched."""
    expected = TokenUser(sub="sram-sub", email="s@example.org", name="S", roles=["admin"])

    resolved = await get_current_user(
        credentials=_credentials("an.oidc.jwt"),
        auth=_oidc_returning(expected),
        session=session,
    )

    assert resolved is expected
    assert resolved.roles == ["admin"], "an OIDC token may still carry roles"


async def test_an_unknown_api_key_is_refused(session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as err:
        await get_current_user(
            credentials=_credentials("msh_not-a-real-key"),
            auth=_oidc_returning(Mock()),
            session=session,
        )

    assert err.value.status_code == 401


async def test_a_revoked_api_key_is_refused(session: AsyncSession) -> None:
    _user, secret, token = await _user_with_key(
        session, slug="apikey03", email="revoked@example.org"
    )
    await revoke_token(session, token)

    with pytest.raises(HTTPException) as err:
        await get_current_user(
            credentials=_credentials(secret),
            auth=_oidc_returning(Mock()),
            session=session,
        )

    assert err.value.status_code == 401


async def test_an_api_key_is_never_sent_to_the_identity_provider(
    session: AsyncSession,
) -> None:
    """Dispatched by prefix, so a hub key is not leaked to SRAM and an OIDC
    failure is never reported for what is plainly a hub key."""
    _user, secret, _token = await _user_with_key(
        session, slug="apikey04", email="prefix@example.org"
    )
    auth = _oidc_returning(Mock())

    await get_current_user(credentials=_credentials(secret), auth=auth, session=session)

    auth.verify_token.assert_not_awaited()
