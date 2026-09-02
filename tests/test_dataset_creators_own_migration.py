"""The backfill gives every ownerless dataset its account's user as owner.

Run against a scratch database migrated to the revision before the backfill,
seeded with the cases it has to handle, then migrated to head.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
_ADMIN_URL = "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/postgres"
_SCRATCH_DB = "metaseed_hub_creators_own_migration_test"
_SCRATCH_URL = f"postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/{_SCRATCH_DB}"
_BEFORE = "260814_drop_feature_grants"

_TENANT = "11111111-1111-1111-1111-111111111111"
_USER = "22222222-2222-2222-2222-222222222222"
_OTHER_TENANT = "33333333-3333-3333-3333-333333333333"
_GONE_USER = "44444444-4444-4444-4444-444444444444"
_OWNED = "55555555-5555-5555-5555-555555555555"
_ORPHAN = "66666666-6666-6666-6666-666666666666"
_NO_LIVE_USER = "77777777-7777-7777-7777-777777777777"
_DEMOTED = "88888888-8888-8888-8888-888888888888"


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


async def _recreate_scratch_database() -> None:
    engine = create_async_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{_SCRATCH_DB}"'))
    finally:
        await engine.dispose()


async def _seed() -> None:
    engine = create_async_engine(_SCRATCH_URL)
    try:
        async with engine.begin() as conn:
            for tenant, slug in ((_TENANT, "seed-a"), (_OTHER_TENANT, "seed-b")):
                await conn.execute(
                    text("INSERT INTO tenants (id, name, slug) VALUES (:id, 'Seed', :slug)"),
                    {"id": tenant, "slug": slug},
                )
            await conn.execute(
                text(
                    "INSERT INTO users (id, tenant_id, keycloak_id, email, display_name) "
                    "VALUES (:id, :tenant, 'kc-seed', 'seed@example.org', 'Seed')"
                ),
                {"id": _USER, "tenant": _TENANT},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, tenant_id, keycloak_id, email, display_name, "
                    "deleted_at) VALUES (:id, :tenant, 'kc-gone', 'gone@example.org', 'Gone', "
                    "now())"
                ),
                {"id": _GONE_USER, "tenant": _OTHER_TENANT},
            )
            for dataset, tenant, name in (
                (_OWNED, _TENANT, "already-owned"),
                (_ORPHAN, _TENANT, "orphan"),
                (_NO_LIVE_USER, _OTHER_TENANT, "nobody-alive"),
                (_DEMOTED, _TENANT, "viewer-only"),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO datasets (id, tenant_id, name, profile, version, data) "
                        "VALUES (:id, :tenant, :name, 'miappe', '1.1', CAST('{}' AS jsonb))"
                    ),
                    {"id": dataset, "tenant": tenant, "name": name},
                )
            await conn.execute(
                text(
                    "INSERT INTO dataset_members (dataset_id, user_id, role) "
                    "VALUES (:dataset, :user, 'owner')"
                ),
                {"dataset": _OWNED, "user": _USER},
            )
            # The account's user holds a lesser role and nobody owns it. The
            # backfill must raise that row, not insert a second one on the
            # same (dataset, user) key: that collision stopped a local
            # `alembic upgrade head` dead.
            await conn.execute(
                text(
                    "INSERT INTO dataset_members (dataset_id, user_id, role) "
                    "VALUES (:dataset, :user, 'viewer')"
                ),
                {"dataset": _DEMOTED, "user": _USER},
            )
    finally:
        await engine.dispose()


async def _owners() -> dict[str, list[str]]:
    engine = create_async_engine(_SCRATCH_URL)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT CAST(dataset_id AS text), CAST(user_id AS text) FROM dataset_members "
                    "WHERE role = 'owner' ORDER BY dataset_id"
                )
            )
            owners: dict[str, list[str]] = {}
            for dataset_id, user_id in rows:
                owners.setdefault(dataset_id, []).append(user_id)
            return owners
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_every_ownerless_dataset_gets_its_accounts_user_as_owner() -> None:
    await _recreate_scratch_database()
    before = _alembic(_BEFORE)
    assert before.returncode == 0, before.stderr
    await _seed()

    head = _alembic("head")
    assert head.returncode == 0, head.stderr

    owners = await _owners()
    assert owners[_ORPHAN] == [_USER], "the orphan gets the account's user"
    assert owners[_OWNED] == [_USER], "an owned dataset gains no second owner row"
    assert _NO_LIVE_USER not in owners, "a deleted user is not made an owner"
    assert owners[_DEMOTED] == [_USER], "an existing lesser role is raised to owner"
    assert await _roles(_DEMOTED, _USER) == ["owner"], "raised, not duplicated"


async def _roles(dataset: str, user: str) -> list[str]:
    engine = create_async_engine(_SCRATCH_URL)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT CAST(role AS text) FROM dataset_members "
                    "WHERE dataset_id = :dataset AND user_id = :user"
                ),
                {"dataset": dataset, "user": user},
            )
            return [role for (role,) in rows]
    finally:
        await engine.dispose()
