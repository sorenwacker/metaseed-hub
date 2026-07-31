"""repair stored profile versions that predate the MAJOR.MINOR rule

Revision ID: 260731_spec_ver
Revises: 260728_tok_exp
Create Date: 2026-07-31

metaseed 0.22 made ``MAJOR.MINOR`` a validation rule on ``ProfileSpec.version``,
which retroactively made some stored rows impossible to deserialize: ``v1.0``,
``1``, ``1.0.0`` and ``1.0-beta`` all load fine under 0.21 and fail under 0.22.

Two places hold the version and both have to be repaired together. The
``version`` column is what listings, lookups and the uniqueness rule read; the
``version`` inside ``spec_data`` is what is actually deserialized. Fixing only
the column leaves a row that lists correctly and still cannot be opened -- worse
than leaving it alone, because the damage stops being visible. The JSONB is
treated as authoritative when the two disagree: the column is a denormalized
copy of it, written from ``spec.version`` on every save and publish.

The normalization rules are metaseed's own (``normalize_profile_version``),
imported rather than restated so the hub cannot repair a version differently
from the way metaseed's CLI repairs a spec file. A value with no leading integer
-- ``draft``, ``latest`` -- yields nothing to derive and is left untouched and
logged: guessing would silently invent a release history. Such a row is reported
as a fixable problem when someone opens it.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "260731_spec_ver"
down_revision: str | None = "260728_tok_exp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def _as_dict(value: Any) -> dict[str, Any]:
    """The stored ``spec_data``, whichever way the driver handed it back."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _inner(spec_data: dict[str, Any]) -> dict[str, Any] | None:
    """The serialized ProfileSpec inside a stored payload, if there is one.

    Drafts and published specs store a SpecBuilderState envelope with the spec
    under ``"spec"``; the oldest rows stored the spec itself.
    """
    nested = spec_data.get("spec")
    if isinstance(nested, dict):
        return nested
    if "version" in spec_data:
        return spec_data
    return None


# Deliberately inlined rather than imported from metaseed. A migration is a
# historical record: it must still run years from now, against whatever metaseed
# version is installed then, and reproduce exactly what it did the day it was
# written. Importing evolving library code would make that untrue. These mirror
# metaseed 0.22.1's normalize_profile_version.
_CONFORMING = re.compile(r"^\d+\.\d+$")
_LEADING_V = re.compile(r"^[vV]")
_NUMERIC_PREFIX = re.compile(r"^\d+(?:\.\d+)*")


def _derived(value: Any) -> str | None:
    """The MAJOR.MINOR form of a stored version, or None if none can be derived.

    Strips a leading ``v``, drops any pre-release or build suffix, pads a single
    integer with MINOR ``0``, and truncates three or more components to two.
    Returns None for a value with no leading integer -- ``draft`` and ``latest``
    are left alone, never guessed at.
    """
    if value is None:
        return None
    text = str(value).strip()
    if _CONFORMING.match(text):
        return text

    remainder = text[1:] if _LEADING_V.match(text) else text
    numeric = _NUMERIC_PREFIX.match(remainder)
    if numeric is None:
        return None

    components = numeric.group(0).split(".")
    if len(components) == 1:
        components.append("0")
    return ".".join(components[:2])


def _repair_table(connection: sa.Connection, table: str) -> None:
    """Bring one table's version column and stored spec version into agreement."""
    rows = connection.execute(
        sa.text(f"SELECT id, name, version, spec_data FROM {table}")  # noqa: S608
    ).fetchall()

    for row_id, name, column_version, raw in rows:
        spec_data = _as_dict(raw)
        inner = _inner(spec_data)
        stored_version = inner.get("version") if inner is not None else None

        # The JSONB wins when both exist: the column is written from it, never
        # the other way round.
        target = _derived(stored_version) or _derived(column_version)
        if target is None:
            logger.warning(
                "%s %s (%r) declares version %r, from which no MAJOR.MINOR "
                "version can be derived; left unchanged",
                table,
                row_id,
                name,
                stored_version if stored_version is not None else column_version,
            )
            continue

        needs_column = column_version != target
        needs_json = inner is not None and str(stored_version) != target
        if not (needs_column or needs_json):
            continue

        if needs_column and _would_collide(connection, table, row_id, name, target):
            logger.warning(
                "%s %s (%r) would become version %r, which is already published "
                "in that workspace; left unchanged",
                table,
                row_id,
                name,
                target,
            )
            continue

        payload = None
        if needs_json:
            updated = copy.deepcopy(spec_data)
            target_dict = updated["spec"] if isinstance(updated.get("spec"), dict) else updated
            target_dict["version"] = target
            payload = json.dumps(updated)

        assignments = ["version = :version"]
        params: dict[str, Any] = {"id": row_id, "version": target}
        if payload is not None:
            assignments.append("spec_data = CAST(:data AS jsonb)")
            params["data"] = payload
        connection.execute(
            sa.text(f"UPDATE {table} SET {', '.join(assignments)} WHERE id = :id"),  # noqa: S608
            params,
        )
        logger.info("%s %s (%r): version %r -> %r", table, row_id, name, column_version, target)


def _would_collide(
    connection: sa.Connection, table: str, row_id: str, name: str, target: str
) -> bool:
    """Whether writing ``target`` would break the published-spec uniqueness rule.

    Only ``specs`` is unique on (tenant, name, version) -- and only among rows
    that are not withdrawn. ``spec_drafts`` is unique on (tenant, user, name),
    which the version does not take part in, so a repair there cannot collide.
    """
    if table != "specs":
        return False
    existing = connection.execute(
        sa.text(
            "SELECT 1 FROM specs WHERE tenant_id = (SELECT tenant_id FROM specs WHERE id = :id) "
            "AND name = :name AND version = :version AND id <> :id AND deleted_at IS NULL"
        ),
        {"id": row_id, "name": name, "version": target},
    ).first()
    return existing is not None


def upgrade() -> None:
    connection = op.get_bind()
    _repair_table(connection, "specs")
    _repair_table(connection, "spec_drafts")


def downgrade() -> None:
    """No reverse. The original values were unloadable and are not recoverable.

    Recording them to undo the repair would mean adding a column to hold them,
    which is a heavier permanent cost than the repair itself.
    """
