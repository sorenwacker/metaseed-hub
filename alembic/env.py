"""Alembic migration environment configuration."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from metaseed_hub.config import get_settings
from metaseed_hub.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables managed by our application
OUR_TABLES = {
    "tenants",
    "teams",
    "team_memberships",
    "users",
    "workspaces",
    "workspace_teams",
    "projects",
    "notes",
    "chat_messages",
    "specs",
    "spec_drafts",
    "alembic_version",
}


def include_object(
    object: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Filter objects to only include our tables, not Keycloak's."""
    if type_ == "table":
        return name in OUR_TABLES if name else False
    if type_ == "index" and hasattr(object, "table"):
        return object.table.name in OUR_TABLES  # type: ignore[union-attr]
    if type_ == "foreign_key_constraint":
        # Handle both reflected and model-defined FKs
        if hasattr(object, "parent") and hasattr(object.parent, "name"):
            # Reflected FK - parent is the Table
            return object.parent.name in OUR_TABLES  # type: ignore[union-attr]
        if hasattr(object, "parent") and hasattr(object.parent, "table"):
            # Model-defined FK - parent is the Column, parent.table is the Table
            return object.parent.table.name in OUR_TABLES  # type: ignore[union-attr]
    return True


def get_url() -> str:
    """Get database URL from settings."""
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, though an
    Engine is acceptable here as well. By skipping the Engine creation we
    don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        # A column the model gives a server default but the migration does not
        # is invisible to the tests — they build tables from the metadata, which
        # carries the default — and fails on the first insert in production.
        # That happened to seek_connections.created_at.
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
