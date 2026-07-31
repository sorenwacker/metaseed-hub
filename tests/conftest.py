"""Pytest fixtures for metaseed-hub tests."""

import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from metaseed_hub.models import Base


def _test_database_url() -> str:
    """The database every fixture in this run should touch.

    Overridable so parallel test runs can use separate databases; the session
    fixture drops and recreates every table, so two runs sharing one database
    deadlock.

    Returns:
        A SQLAlchemy async URL.
    """
    return os.environ.get(
        "METASEED_HUB_TEST_DB_URL",
        "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/metaseed_hub_test",
    )


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Create a database session with automatic rollback.

    Creates tables before each test and rolls back after.

    Yields:
        AsyncSession for database operations.
    """
    engine = create_async_engine(_test_database_url(), echo=False)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session_maker() as session:
        yield session

    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def server(session: AsyncSession) -> AsyncGenerator[object, None]:
    """The MCP server, with the app-wide database connected.

    The tools open their own session per call, from ``db``, because each call is
    a different caller. That is the behaviour under test, so the tests connect
    ``db`` rather than substituting the fixture's session.

    Args:
        session: The per-test session fixture, ordered first so the schema
            exists before the server's own connections are used.

    Yields:
        A configured FastMCP server.
    """
    from metaseed_hub.database import db
    from metaseed_hub.mcp import create_mcp_server

    await db.connect(_test_database_url())
    try:
        yield create_mcp_server()
    finally:
        await db.disconnect()
