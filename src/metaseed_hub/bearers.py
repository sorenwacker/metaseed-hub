"""One answer to "who presented this bearer?", for the REST API and the MCP endpoint.

Three credentials arrive in the same ``Authorization: Bearer`` header, told
apart by shape: a hub personal access token (``msh_...``), an OIDC access
token from the sign-in (a JWT, three dot-separated parts), and a SRAM
application token (anything else, checked against SRAM). The REST dependency
and the MCP caller each kept their own copy of the first two cases, which is
how a third would have been added twice; this is the one place now.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import select

from metaseed_hub.auth import TokenUser, verify_token
from metaseed_hub.config import get_settings
from metaseed_hub.models import User
from metaseed_hub.sram_tokens import (
    IntrospectionCredentialError,
    IntrospectionUnavailableError,
    introspect,
)
from metaseed_hub.tokens import TOKEN_PREFIX, authenticate_token

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BearerError(Exception):
    """The bearer did not authenticate, with the status a client should get.

    401 is a refusal; 503 means the hub could not check, which is not the
    same thing and must not be reported as one.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Bearer:
    """Who a bearer acts as: the account, and the identity a route may read."""

    user: User | None
    """The hub account, or None for an OIDC bearer whose subject has not
    signed in to the hub yet: the browser path never needed one, and the
    REST routes that do resolve it themselves."""
    token_user: TokenUser
    kind: str
    """``hub``, ``oidc`` or ``sram``."""


def default_http_client() -> httpx.AsyncClient:
    """The client used to reach SRAM when none is injected (a test seam)."""
    return httpx.AsyncClient(timeout=10.0)


def _looks_like_jwt(presented: str) -> bool:
    return presented.count(".") == 2 and all(presented.split("."))


async def _account(session: AsyncSession, sub: str) -> User | None:
    result = await session.execute(
        select(User).where(User.keycloak_id == sub, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def resolve_bearer(
    session: AsyncSession,
    presented: str,
    *,
    verify_oidc: Callable[[str], Awaitable[TokenUser]] = verify_token,
    http_client: httpx.AsyncClient | None = None,
) -> Bearer:
    """The account a bearer acts as, or why it does not.

    Args:
        session: Database session.
        presented: The bearer as presented.
        verify_oidc: How an OIDC access token is verified; the REST dependency
            passes its configured verifier, tests a stub.
        http_client: The client to reach SRAM with; one is made otherwise.

    Raises:
        BearerError: 401 when the credential is refused, 503 when SRAM could
            not be asked.
    """
    if presented.startswith(TOKEN_PREFIX):
        user = await authenticate_token(session, presented)
        if user is None:
            raise BearerError(401, "That access token is not valid, has expired, or was revoked.")
        # A hub token carries no roles and no entitlements: it acts for the
        # person's own data and must not confer what only the IdP can.
        return Bearer(user, _token_user(user, entitlements=()), "hub")

    if _looks_like_jwt(presented):
        try:
            token_user = await verify_oidc(presented)
        except Exception as exc:
            raise BearerError(401, "That OIDC access token is not valid.") from exc
        return Bearer(await _account(session, token_user.sub), token_user, "oidc")

    settings = get_settings()
    if not settings.sram_introspection_token:
        raise BearerError(
            401,
            "SRAM application tokens are not enabled on this hub; "
            "use a personal access token from your profile.",
        )
    client = http_client or default_http_client()
    try:
        answer = await introspect(presented, settings=settings, http_client=client)
    except IntrospectionCredentialError:
        raise BearerError(
            503, "The hub's SRAM credential was rejected; an administrator has to renew it."
        ) from None
    except IntrospectionUnavailableError as exc:
        raise BearerError(
            503, f"The token was not checked: SRAM could not be reached ({exc})."
        ) from exc
    finally:
        if http_client is None:
            await client.aclose()
    if not answer.active:
        raise BearerError(401, f"SRAM refused the token: {answer.status}.")
    user = await _account(session, answer.sub)
    if user is None:
        raise BearerError(401, _SIGN_IN_FIRST)
    # SRAM's answer names the person's current groups: take a reading, as a
    # sign-in does, so a collaboration grant reaches this client too.
    from metaseed_hub.collaborations import record_memberships

    await record_memberships(session, user.id, answer.entitlements)
    await session.commit()
    return Bearer(user, _token_user(user, entitlements=answer.entitlements), "sram")


_SIGN_IN_FIRST = (
    "The token verified, but no hub account exists for it yet; sign in to the hub once first."
)


def _token_user(user: User, *, entitlements: tuple[str, ...]) -> TokenUser:
    return TokenUser(
        sub=user.keycloak_id,
        email=user.email,
        name=user.display_name or user.email,
        roles=[],
        entitlements=list(entitlements),
    )
