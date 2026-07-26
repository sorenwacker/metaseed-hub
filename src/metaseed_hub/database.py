"""Centralized database session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """Manages database connections and session creation.

    This class provides centralized management of the async SQLAlchemy engine
    and session factory. It should be initialized once during application
    startup and used throughout the application lifecycle.

    Example:
        db = Database()
        await db.connect("postgresql+asyncpg://...")

        async for session in db.session():
            # use session

        await db.disconnect()
    """

    def __init__(self) -> None:
        """Initialize the database manager."""
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self, url: str, *, echo: bool = False) -> None:
        """Create the database engine and session factory.

        Args:
            url: Database connection URL (must use async driver like asyncpg).
            echo: If True, log all SQL statements.
        """
        self._engine = create_async_engine(url, echo=echo)
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )

    async def disconnect(self) -> None:
        """Close the database engine and release connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    @property
    def engine(self) -> AsyncEngine:
        """Return the database engine.

        Raises:
            RuntimeError: If connect() has not been called.
        """
        if self._engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the session factory.

        Raises:
            RuntimeError: If connect() has not been called.
        """
        if self._session_factory is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._session_factory

    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a database session.

        Intended for use as an async context manager or FastAPI dependency.

        Yields:
            AsyncSession for database operations.

        Raises:
            RuntimeError: If connect() has not been called.
        """
        async with self.session_factory() as session:
            yield session


# Global database instance for the application
db = Database()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session.

    Yields:
        AsyncSession for database operations.
    """
    async for session in db.session():
        yield session


__all__ = ["Database", "db", "get_session"]
