"""record whether a seek connection works

A connection is stored whether or not it verifies: refusing to save what was
just typed means retyping the API key to fix a typo in the URL. The outcome of
the last check is kept alongside it, and shown wherever the connection appears.

Revision ID: 260810_seek_status
Revises: 260810_seek_connections
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "260810_seek_status"
down_revision: str | None = "260810_seek_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "seek_connections",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("seek_connections", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("seek_connections", "last_error")
    op.drop_column("seek_connections", "verified_at")
