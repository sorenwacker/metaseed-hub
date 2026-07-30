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


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Create a database session with automatic rollback.

    Creates tables before each test and rolls back after.

    Yields:
        AsyncSession for database operations.
    """
    # Overridable so parallel test runs can use separate databases; the fixture
    # drops and recreates every table, so two runs sharing one database deadlock.
    url = os.environ.get(
        "METASEED_HUB_TEST_DB_URL",
        "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/metaseed_hub_test",
    )
    engine = create_async_engine(url, echo=False)

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
