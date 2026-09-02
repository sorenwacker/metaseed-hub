"""Pytest fixtures for metaseed-hub tests."""

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from metaseed_hub.models import Base


def _test_database_url() -> str:
    """The database every fixture in this run should touch.

    Overridable so parallel test runs can use separate databases: the schema is
    created once per run and every table is emptied between tests, so two runs
    sharing one database would clear each other's data.

    Returns:
        A SQLAlchemy async URL.
    """
    return os.environ.get(
        "METASEED_HUB_TEST_DB_URL",
        "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/metaseed_hub_test",
    )


# Single-process only: each pytest-xdist worker would see its own copy of this
# flag, rebuild the schema, and race the others. Point workers at separate
# databases via METASEED_HUB_TEST_DB_URL before parallelising.
_schema_created = False


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """A database session, with every table emptied afterwards.

    The schema is built once per run rather than per test. Rebuilding it each
    time was most of the suite's runtime, and left a worse problem behind: a
    killed run's connections keep holding locks, so the next run's ``drop_all``
    blocks rather than failing. That looks like a hang and is hard to place.

    Emptied rather than rolled back, because the ``server`` fixture connects
    separately and its tools open their own sessions -- a test's data has to be
    committed to be visible to them.

    Yields:
        AsyncSession for database operations.
    """
    global _schema_created

    engine = create_async_engine(_test_database_url(), echo=False)
    if not _schema_created:
        async with engine.begin() as conn:
            # A killed run leaves connections behind that keep holding locks, so
            # drop_all blocks forever instead of failing. Clear them first: this
            # is a test database, and anything still attached to it is a corpse.
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )
        async with engine.begin() as conn:
            # The whole schema, not drop_all: a table whose model has been
            # deleted is invisible to the metadata, stays behind, and its
            # foreign key then blocks dropping the tables that are still
            # declared. Removing teams and notes turned every test in the run
            # into an error until the leftovers were dropped by hand.
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.run_sync(Base.metadata.create_all)
        _schema_created = True

    async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session_maker() as session:
        yield session

    # DELETE rather than TRUNCATE: TRUNCATE needs an ACCESS EXCLUSIVE lock on
    # every table at once and deadlocks against the ``server`` fixture's second
    # connection pool. DELETE takes ordinary row locks. Children first, so
    # foreign keys never block the sweep.
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'DELETE FROM "{table.name}"'))
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


def app_templates() -> Any:
    """Templates loaded the way the running app loads them.

    The app searches the hub's template directory first and falls back to
    metaseed's, so a hub-only environment cannot render any template the hub
    deliberately does not own. Tests that render one must use the same loader or
    they fail on a template the application resolves perfectly well.

    Returns:
        A Jinja2Templates with the application's ChoiceLoader.
    """
    from fastapi.templating import Jinja2Templates
    from jinja2 import ChoiceLoader, FileSystemLoader

    from metaseed_hub.ui.app import TEMPLATES_DIR
    from metaseed_hub.ui.metaseed_ui import METASEED_TEMPLATES_DIR

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(TEMPLATES_DIR)),
            FileSystemLoader(str(METASEED_TEMPLATES_DIR)),
        ]
    )
    return templates


@pytest.fixture
async def app_db(session):
    """The app-wide connection the routes open their own sessions from."""
    from metaseed_hub.database import db

    await db.connect(_test_database_url())
    yield
    await db.disconnect()


@pytest.fixture
async def ena_dataset(session):
    """An empty ena dataset in the account of the user rendered-page tests sign in as."""
    from metaseed_hub.ui.dependencies import tenant_slug_for
    from tests.factories import make_dataset, make_tenant, make_user

    tenant = make_tenant(slug=tenant_slug_for("kc-1"))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-1", email="u@example.org")
    session.add(user)
    dataset = make_dataset(tenant=tenant, profile="ena", version="1.0")
    session.add(dataset)
    await session.commit()
    return dataset
