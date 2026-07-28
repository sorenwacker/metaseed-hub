"""Personal access tokens, for clients that cannot hold a browser session.

The hub authenticates people through OIDC, which needs a browser. An MCP client
is not a browser, so it presents a token instead — issued to a user, and standing
in for exactly that user's identity, so a tool call can be scoped to the tenant
that owns the data.

Only the SHA-256 hash of a token is stored. The secret is shown once at creation
and cannot be recovered, so a copy of the database is not a set of working
credentials.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import ApiToken, User

TOKEN_PREFIX = "msh_"
"""Marks a string as a hub token, so a leaked one is recognisable in a log or a
secret scanner."""

# 32 bytes of entropy, url-safe. Long enough that guessing is not a threat model.
_TOKEN_BYTES = 32


def _hash(secret: str) -> str:
    """The stored form of a token.

    A plain SHA-256, not a password hash: the secret is 256 bits of random, so
    there is no dictionary to attack and no reason to make each lookup slow.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def token_from_header(header: str | None) -> str | None:
    """Extract a bearer token from an ``Authorization`` header.

    Strict about the scheme: accepting a bare value would let a Basic
    credential, or anything else a client sent, be treated as a token.

    Returns:
        The token, or ``None`` if the header is absent or not a bearer.
    """
    if not header:
        return None
    parts = header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


async def issue_token(session: AsyncSession, user: User, *, name: str) -> tuple[str, ApiToken]:
    """Create a token for ``user`` and return ``(secret, record)``.

    The secret is the only time the caller sees it; it is not stored and cannot
    be shown again.

    Args:
        session: Database session.
        user: The user the token acts as.
        name: What the user calls it, so two tokens are distinguishable.
    """
    secret = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    token = ApiToken(user_id=user.id, name=name, token_hash=_hash(secret))
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return secret, token


async def authenticate_token(session: AsyncSession, secret: str) -> User | None:
    """Return the user a token acts as, or None.

    Records the use, so a token nobody presents is visible as such and can be
    cleaned up. A revoked token authenticates as nobody.
    """
    if not secret:
        return None

    result = await session.execute(select(ApiToken).where(ApiToken.token_hash == _hash(secret)))
    token = result.scalar_one_or_none()
    if token is None or not token.is_active:
        return None

    token.last_used_at = datetime.now(UTC)
    await session.commit()

    return await session.get(User, token.user_id)


async def revoke_token(session: AsyncSession, token: ApiToken) -> None:
    """Withdraw a token, keeping the record.

    A timestamp rather than a delete: an admin needs to see that a token existed
    and when it stopped working.
    """
    token.revoked_at = datetime.now(UTC)
    await session.commit()


async def active_tokens(session: AsyncSession, user: User) -> list[ApiToken]:
    """The user's tokens that still work, newest first.

    Revoked ones are kept in the database for the audit trail but are not shown
    back to the user, who can only act on the live ones.
    """
    result = await session.execute(
        select(ApiToken)
        .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return list(result.scalars().all())
