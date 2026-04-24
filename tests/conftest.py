"""Pytest fixtures for metaseed-hub tests."""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from metaseed_hub.models import Base


@pytest.fixture(scope="session")
def database_url() -> str:
    """Return the test database URL.

    Uses a separate test database to avoid affecting development data.
    """
    return "postgresql+asyncpg://metaseed:metaseed_dev@localhost:5432/metaseed_hub_test"


@pytest.fixture(scope="session")
async def engine(database_url: str):
    """Create the async engine for the test session.

    Args:
        database_url: Test database connection string.

    Yields:
        AsyncEngine connected to the test database.
    """
    engine = create_async_engine(database_url, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def setup_database(engine):
    """Create all tables at the start of the test session.

    Args:
        engine: The async database engine.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def connection(engine, setup_database) -> AsyncGenerator[AsyncConnection, None]:
    """Create a connection with a transaction that rolls back after each test.

    This provides test isolation without requiring table recreation.

    Args:
        engine: The async database engine.
        setup_database: Fixture to ensure tables exist.

    Yields:
        AsyncConnection with an active transaction.
    """
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield conn
        finally:
            await trans.rollback()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """Create an async session bound to the test transaction.

    Uses nested transactions (savepoints) to allow commit() calls in tests
    while still rolling back at the end.

    Args:
        connection: The connection with the outer transaction.

    Yields:
        AsyncSession for database operations.
    """

    def start_nested(session: Session, _transaction) -> None:
        """Restart savepoint when the nested transaction ends."""
        if not connection.sync_connection.in_nested_transaction():
            connection.sync_connection.begin_nested()

    async_session_maker = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async with async_session_maker() as session:
        # Listen for after_transaction_end to restart savepoints
        event.listen(
            session.sync_session,
            "after_transaction_end",
            start_nested,
        )

        await connection.begin_nested()
        yield session
