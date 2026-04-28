"""Pytest fixtures for metaseed-hub tests."""

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
    url = "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/metaseed_hub_test"
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
