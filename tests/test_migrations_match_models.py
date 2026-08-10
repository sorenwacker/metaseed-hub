"""The migrated schema must match what the models declare.

A column the model gives a server default but the migration does not is
invisible to every other test — they build tables from the metadata, which
carries the default — and fails on the first insert in production. That is
exactly how ``seek_connections.created_at`` reached production broken.

``alembic check`` runs with ``compare_server_default=True`` (``alembic/env.py``),
so this test fails on that divergence rather than a user finding it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.timeout(120)
def test_the_migrations_produce_the_models_schema() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "The migrations no longer match the models — including server defaults:\n"
        f"{result.stdout}\n{result.stderr}"
    )
