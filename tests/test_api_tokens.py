"""Personal access tokens for clients that cannot hold a browser session.

A token stands in for a user, so the failure that matters is one authenticating
as somebody else — or surviving revocation. Every assertion here is about which
user a token resolves to, not merely that it resolves.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import ApiToken
from metaseed_hub.tokens import (
    authenticate_token,
    issue_token,
    revoke_token,
    token_from_header,
)
from tests.factories import make_tenant, make_user


async def _user(session: AsyncSession, email: str = "a@example.org"):
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=email)
    session.add(user)
    await session.commit()
    return user


async def test_the_secret_is_returned_once_and_never_stored(
    session: AsyncSession,
) -> None:
    """A database copy must not be replayable: only the hash is kept."""
    user = await _user(session)

    secret, token = await issue_token(session, user, name="laptop")

    assert secret
    stored = (await session.execute(select(ApiToken))).scalars().all()
    assert len(stored) == 1
    assert secret not in stored[0].token_hash
    assert stored[0].token_hash != secret
    assert token.name == "laptop"


async def test_a_token_authenticates_as_the_user_it_was_issued_to(
    session: AsyncSession,
) -> None:
    alice = await _user(session, "alice@example.org")
    bob = await _user(session, "bob@example.org")
    alice_secret, _ = await issue_token(session, alice, name="alice's")
    bob_secret, _ = await issue_token(session, bob, name="bob's")

    assert (await authenticate_token(session, alice_secret)).id == alice.id
    assert (await authenticate_token(session, bob_secret)).id == bob.id


async def test_an_unknown_secret_authenticates_as_nobody(
    session: AsyncSession,
) -> None:
    await _user(session)

    assert await authenticate_token(session, "msh_not-a-real-token") is None
    assert await authenticate_token(session, "") is None


async def test_a_revoked_token_stops_working_but_is_still_on_record(
    session: AsyncSession,
) -> None:
    user = await _user(session)
    secret, token = await issue_token(session, user, name="old laptop")

    await revoke_token(session, token)

    assert await authenticate_token(session, secret) is None
    stored = (await session.execute(select(ApiToken))).scalars().all()
    assert len(stored) == 1, "revocation is a timestamp, not a delete"
    assert stored[0].revoked_at is not None


async def test_using_a_token_records_when(session: AsyncSession) -> None:
    """A token nobody has used is visible as such, so it can be cleaned up."""
    user = await _user(session)
    secret, token = await issue_token(session, user, name="laptop")
    assert token.last_used_at is None

    before = datetime.now(UTC)
    await authenticate_token(session, secret)

    await session.refresh(token)
    assert token.last_used_at is not None
    assert token.last_used_at >= before.replace(microsecond=0)


async def test_two_tokens_never_collide(session: AsyncSession) -> None:
    user = await _user(session)

    secrets = {(await issue_token(session, user, name=f"t{i}"))[0] for i in range(20)}

    assert len(secrets) == 20


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer msh_abc", "msh_abc"),
        ("bearer msh_abc", "msh_abc"),
        ("  Bearer   msh_abc  ", "msh_abc"),
        ("Basic msh_abc", None),
        ("msh_abc", None),
        ("Bearer", None),
        (None, None),
    ],
)
def test_the_header_is_parsed_strictly(header: str | None, expected: str | None) -> None:
    """A lax parser that accepted a bare value would let a Basic credential
    through as a bearer token."""
    assert token_from_header(header) == expected


async def test_deleting_a_user_takes_their_tokens_with_them(
    session: AsyncSession,
) -> None:
    """A token outliving its owner would authenticate as a deleted account."""
    user = await _user(session)
    await issue_token(session, user, name="laptop")

    await session.delete(user)
    await session.commit()

    assert (await session.execute(select(ApiToken))).scalars().all() == []
