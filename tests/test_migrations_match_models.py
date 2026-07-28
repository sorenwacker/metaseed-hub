"""The migrations must produce the schema the models describe.

Every other test builds its schema with ``Base.metadata.create_all``, so the
suite is green whether or not the Alembic migrations work at all. That gap is
not hypothetical: ``260728_spec_unpub`` shipped with ``op.inline_literal``,
which rendered the partial index predicate as the string ``'deleted_at IS
NULL'`` and made ``alembic upgrade head`` fail outright, while the full suite
passed.

This runs the migrations exactly as a deploy does — a fresh database, the real
``alembic upgrade head`` — and then asks Alembic to diff the result against the
models. Anything it reports is drift between what a new deployment gets and what
the code expects.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from metaseed_hub.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent

_ADMIN_URL = "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/postgres"
_SCRATCH_DB = "metaseed_hub_migration_test"
_SCRATCH_URL = f"postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/{_SCRATCH_DB}"

# Alembic's own bookkeeping table is not in the models and never should be.
_IGNORED_TABLES = {"alembic_version"}


async def _recreate_scratch_database() -> None:
    """Drop and recreate the scratch database.

    A migration run is only meaningful from empty: against a database that
    already has the tables, ``upgrade head`` is a no-op and proves nothing.
    """
    engine = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text(f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{_SCRATCH_DB}"'))
    finally:
        await engine.dispose()


def _run_migrations() -> subprocess.CompletedProcess[str]:
    """Run ``alembic upgrade head`` against the scratch database."""
    env = {
        **os.environ,
        # env.py reads the URL from settings, which reads this.
        "DATABASE_URL": _SCRATCH_URL,
        "SECRET_KEY": "x" * 40,
    }
    return subprocess.run(
        ["uv", "run", "--no-sync", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _describe(diff: object) -> str:
    """Render one Alembic diff entry readably for a failure message."""
    if isinstance(diff, list):
        return "\n".join(_describe(entry) for entry in diff)
    if isinstance(diff, tuple) and diff:
        return f"  {diff[0]}: {diff[1:]}"
    return f"  {diff}"


@pytest.mark.asyncio
async def test_upgrade_head_succeeds_on_an_empty_database() -> None:
    """A broken migration must fail the suite, not just a deploy."""
    await _recreate_scratch_database()

    result = _run_migrations()

    assert (
        result.returncode == 0
    ), f"alembic upgrade head failed on a fresh database:\n{result.stdout}\n{result.stderr}"


@pytest.mark.asyncio
async def test_the_migrated_schema_matches_the_models() -> None:
    """No drift between what a deploy gets and what the code expects."""
    await _recreate_scratch_database()
    result = _run_migrations()
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    engine = create_async_engine(_SCRATCH_URL)
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(
                        sync_conn,
                        opts={
                            "include_object": lambda obj, name, type_, reflected, compare_to: (
                                not (type_ == "table" and name in _IGNORED_TABLES)
                            )
                        },
                    ),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()

    assert not diffs, (
        "The migrations do not produce the schema the models describe.\n"
        "Each entry is a change Alembic would have to make to the migrated\n"
        "database to reach the models — so it is missing from the migrations:\n"
        f"{_describe(diffs)}"
    )
