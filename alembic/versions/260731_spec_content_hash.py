"""record the content hash of every published specification

Revision ID: 260731_spec_hash
Revises: 260731_spec_ver
Create Date: 2026-07-31

A version says how a specification relates to its predecessor; it does not
identify one. Two releases can both declare ``cinema 1.1`` and hold different
content, so datasets that record only the version cannot tell whether the
specification they were written against is the one now in force.

The column is backfilled from each row's stored ``spec_data``, so specifications
published before this existed get their identity too rather than only new ones.
A row whose stored spec cannot be deserialized is left null: a hash that names
nothing is worse than no hash. This runs after the version repair, so rows that
were only unreadable because of a pre-MAJOR.MINOR version are hashable by now.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from metaseed.specs import content_hash
from metaseed.specs.schema import ProfileSpec

from alembic import op

revision: str = "260731_spec_hash"
down_revision: str | None = "260731_spec_ver"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# "sha256:" plus 64 hex digits.
HASH_LENGTH = 71


def _spec_payload(raw: Any) -> dict[str, Any] | None:
    """The serialized ProfileSpec inside a stored ``spec_data``, if there is one."""
    if isinstance(raw, str) and raw:
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    nested = raw.get("spec")
    if isinstance(nested, dict):
        return nested
    return raw if "entities" in raw else None


def upgrade() -> None:
    op.add_column("specs", sa.Column("content_hash", sa.String(length=HASH_LENGTH), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, name, spec_data FROM specs")).fetchall()
    for row_id, name, raw in rows:
        payload = _spec_payload(raw)
        if payload is None:
            logger.warning("spec %s (%r) holds no specification; no content hash", row_id, name)
            continue
        try:
            digest = content_hash(ProfileSpec.model_validate(payload))
        except Exception as exc:
            logger.warning(
                "spec %s (%r) could not be read, so it has no content hash: %s",
                row_id,
                name,
                exc,
            )
            continue
        connection.execute(
            sa.text("UPDATE specs SET content_hash = :hash WHERE id = :id"),
            {"hash": digest, "id": row_id},
        )


def downgrade() -> None:
    op.drop_column("specs", "content_hash")
