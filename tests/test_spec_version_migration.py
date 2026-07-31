"""The version-repair migration, run the way a deploy runs it.

metaseed 0.22 made ``MAJOR.MINOR`` a validation rule, which retroactively made
some stored rows unloadable. The repair has to fix two places at once: the
``version`` column, which every listing and lookup reads, and the ``version``
inside the ``spec_data`` JSONB, which is what actually gets deserialized. A
migration that fixed one and not the other would leave a row that lists fine and
still cannot be opened -- worse than leaving it alone, because the damage would
no longer be visible.

So this seeds rows at the revision before the migration, runs the real
``alembic upgrade head``, and reads both places back. The normalization rules
themselves are metaseed's and are tested there; what is asserted here is that
the migration applies them, applies them to both places, and leaves alone the
one value it must not guess at.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent

_ADMIN_URL = "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/postgres"
_SCRATCH_DB = "metaseed_hub_version_migration_test"
_SCRATCH_URL = f"postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/{_SCRATCH_DB}"

# The revision immediately before the version repair, so the seeded rows exist
# in exactly the state the repair has to find them in.
_BEFORE_REPAIR = "260728_tok_exp"

_TENANT = "11111111-1111-1111-1111-111111111111"
_USER = "22222222-2222-2222-2222-222222222222"


def _spec_data(name: str, version: str) -> str:
    """A stored SpecBuilderState envelope declaring ``version``."""
    return json.dumps(
        {
            "spec": {
                "name": name,
                "version": version,
                "root_entity": "Sample",
                "entities": {"Sample": {"description": "a sample", "fields": []}},
                "validation_rules": [],
            },
            "editing_entity": None,
            "editing_field_idx": None,
            "editing_rule_idx": None,
            "template_source": None,
            "has_unsaved_changes": False,
        }
    )


async def _recreate_scratch_database() -> None:
    engine = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{_SCRATCH_DB}"'))
    finally:
        await engine.dispose()


def _alembic(target: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": _SCRATCH_URL, "SECRET_KEY": "x" * 40}
    return subprocess.run(
        ["uv", "run", "--no-sync", "alembic", "upgrade", target],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


async def _seed() -> None:
    """Insert one row per case the repair has to handle."""
    engine = create_async_engine(_SCRATCH_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Seed', 'seed-migration')"),
                {"id": _TENANT},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, tenant_id, keycloak_id, email, display_name) "
                    "VALUES (:id, :tenant, 'kc-seed', 'seed@example.org', 'Seed')"
                ),
                {"id": _USER, "tenant": _TENANT},
            )
            for name, version in (
                ("leading-v", "v1.0"),
                ("three-components", "1.0.0"),
                ("prerelease", "2.0-beta"),
                ("conforming", "3.1"),
                ("underivable", "draft"),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO specs (id, tenant_id, name, version, spec_data, status, "
                        "created_by_id) VALUES (gen_random_uuid(), :tenant, :name, :version, "
                        "CAST(:data AS jsonb), 'published', :user)"
                    ),
                    {
                        "tenant": _TENANT,
                        "name": name,
                        "version": version,
                        "data": _spec_data(name, version),
                        "user": _USER,
                    },
                )
            for name, version in (("draft-bare-major", "1"), ("draft-conforming", "0.1")):
                await conn.execute(
                    text(
                        "INSERT INTO spec_drafts (id, tenant_id, user_id, name, version, "
                        "spec_data) VALUES (gen_random_uuid(), :tenant, :user, :name, :version, "
                        "CAST(:data AS jsonb))"
                    ),
                    {
                        "tenant": _TENANT,
                        "user": _USER,
                        "name": name,
                        "version": version,
                        "data": _spec_data(name, version),
                    },
                )
    finally:
        await engine.dispose()


async def _rows(table: str) -> dict[str, dict[str, Any]]:
    """``{name: {column_version, json_version, content_hash}}`` for a table."""
    engine = create_async_engine(_SCRATCH_URL)
    columns = "name, version, spec_data #>> '{spec,version}'"
    if table == "specs":
        columns += ", content_hash"
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(f"SELECT {columns} FROM {table}"))
            out: dict[str, dict[str, Any]] = {}
            for row in result:
                out[row[0]] = {
                    "column": row[1],
                    "json": row[2],
                    "content_hash": row[3] if table == "specs" else None,
                }
            return out
    finally:
        await engine.dispose()


_MIGRATED: dict[str, dict[str, dict[str, Any]]] | None = None


@pytest_asyncio.fixture
async def migrated() -> dict[str, dict[str, dict[str, Any]]]:
    """The state of both tables after a real upgrade over seeded rows.

    Cached across the module's tests rather than declared module-scoped: the
    suite pins its asyncio loop to function scope, and dropping and re-running
    the migrations once per assertion would cost minutes to prove the same run.
    """
    global _MIGRATED
    if _MIGRATED is None:
        _MIGRATED = await _seed_and_migrate()
    return _MIGRATED


async def _seed_and_migrate() -> dict[str, dict[str, dict[str, Any]]]:
    """Seed at the pre-repair revision, upgrade to head, and read both tables."""
    await _recreate_scratch_database()
    before = _alembic(_BEFORE_REPAIR)
    assert before.returncode == 0, f"{before.stdout}\n{before.stderr}"

    await _seed()

    after = _alembic("head")
    assert after.returncode == 0, (
        f"alembic upgrade head failed over seeded rows:\n{after.stdout}\n{after.stderr}"
    )
    return {"specs": await _rows("specs"), "spec_drafts": await _rows("spec_drafts")}


@pytest.mark.parametrize(
    ("table", "name", "expected"),
    [
        ("specs", "leading-v", "1.0"),
        ("specs", "three-components", "1.0"),
        ("specs", "prerelease", "2.0"),
        ("spec_drafts", "draft-bare-major", "1.0"),
    ],
)
async def test_a_non_conforming_version_is_normalized_in_both_places(
    migrated, table: str, name: str, expected: str
) -> None:
    """The column and the JSONB must agree, or the row lists but will not open."""
    row = migrated[table][name]
    assert row["column"] == expected
    assert row["json"] == expected


@pytest.mark.parametrize(
    ("table", "name", "expected"),
    [("specs", "conforming", "3.1"), ("spec_drafts", "draft-conforming", "0.1")],
)
async def test_a_conforming_version_is_left_alone(
    migrated, table: str, name: str, expected: str
) -> None:
    row = migrated[table][name]
    assert row["column"] == expected
    assert row["json"] == expected


async def test_an_underivable_version_is_left_untouched(migrated) -> None:
    """``draft`` has no leading number, so there is nothing to derive.

    Guessing would silently invent a release history. The row keeps its value
    and is reported as a fixable problem when someone opens it.
    """
    row = migrated["specs"]["underivable"]
    assert row["column"] == "draft"
    assert row["json"] == "draft"


async def test_the_content_hash_is_backfilled_for_readable_rows(migrated) -> None:
    """Existing published specs get their identity too, not only new ones."""
    from metaseed.specs import content_hash
    from metaseed.specs.schema import ProfileSpec

    row = migrated["specs"]["leading-v"]
    expected = content_hash(
        ProfileSpec.model_validate(json.loads(_spec_data("leading-v", "1.0"))["spec"])
    )
    assert row["content_hash"] == expected


async def test_a_row_that_cannot_be_read_gets_no_content_hash(migrated) -> None:
    """A hash that names nothing is worse than no hash."""
    assert migrated["specs"]["underivable"]["content_hash"] is None
