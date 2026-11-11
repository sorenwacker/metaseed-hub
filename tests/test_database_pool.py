"""A pooled connection that Postgres closed must not fail the next request.

Postgres closes a client connection when it restarts, when an administrator
terminates the backend, or when the network between the two drops. The pool
cannot see that happen, and without a liveness check it hands the dead
connection to the next request, which fails with "connection is closed". The
production error log recorded exactly that, always on the first query of a
request.
"""

from __future__ import annotations

import asyncio

import asyncpg
from sqlalchemy import text

from metaseed_hub.database import Database
from tests.conftest import _test_database_url


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def test_a_connection_closed_by_the_server_is_replaced() -> None:
    url = _test_database_url()
    db = Database()
    await db.connect(url)
    try:
        async for session in db.session():
            pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()

        admin = await asyncpg.connect(_asyncpg_dsn(url))
        try:
            assert await admin.fetchval("SELECT pg_terminate_backend($1, 5000)", pid)
        finally:
            await admin.close()

        # The pool has to learn of the close before a checkout can, as it has
        # in production, where the dead connection sits idle for minutes.
        await asyncio.sleep(0.2)

        async for session in db.session():
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await db.disconnect()
