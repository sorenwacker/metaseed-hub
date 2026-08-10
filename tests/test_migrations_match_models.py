"""The migrations must build exactly the schema the models declare.

A column the model gives a server default but the migration does not is
invisible to every other test — they build their tables from the metadata with
``create_all``, which carries the default — and fails on the first insert in
production. That is how ``seek_connections.created_at`` shipped broken.

So this test does what no other test does: it creates an empty database, runs
the migrations into it, and asks alembic whether the result matches the models,
server defaults included (``compare_server_default=True`` in ``alembic/env.py``).
Checking the ordinary test database would prove nothing, since that schema comes
from the models in the first place.
"""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from tests.conftest import _test_database_url

SCRATCH_DB = "metaseed_hub_migration_check"


def _sync_dsn(url: str, database: str | None = None) -> str:
    """The asyncpg DSN for ``url``, optionally pointed at another database."""
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    path = f"/{database}" if database else parts.path
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@pytest.fixture
async def scratch_database() -> str:
    """An empty database, dropped again afterwards."""
    admin = await asyncpg.connect(_sync_dsn(_test_database_url(), "postgres"))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        await admin.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await admin.close()

    yield _test_database_url().rsplit("/", 1)[0] + f"/{SCRATCH_DB}"

    admin = await asyncpg.connect(_sync_dsn(_test_database_url(), "postgres"))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
    finally:
        await admin.close()


def _alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "DATABASE_URL": database_url},
    )


@pytest.mark.timeout(300)
async def test_the_migrations_produce_the_models_schema(scratch_database) -> None:
    upgrade = _alembic("upgrade", "head", database_url=scratch_database)
    assert upgrade.returncode == 0, (
        f"The migrations do not apply to an empty database:\n{upgrade.stderr}"
    )

    check = _alembic("check", database_url=scratch_database)
    assert check.returncode == 0, (
        "The migrated schema does not match the models — including server "
        f"defaults:\n{check.stdout}\n{check.stderr}"
    )
