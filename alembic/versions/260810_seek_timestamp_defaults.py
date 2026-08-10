"""give seek connection timestamps their database defaults

The model declares ``server_default=func.now()`` (TimestampMixin) but the
creating migration wrote plain NOT NULL columns, so an insert that omits them —
which is every insert, since SQLAlchemy leaves server-defaulted columns to the
database — failed with a not-null violation. Tests never saw it: they build
tables from the metadata, which carries the default.

Revision ID: 260810_seek_defaults
Revises: 260810_seek_status
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "260810_seek_defaults"
down_revision: str | None = "260810_seek_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("seek_connections", sa.Column("project_hint", sa.String(255), nullable=True))
    for column in ("created_at", "updated_at"):
        op.alter_column("seek_connections", column, server_default=sa.func.now(), nullable=False)


def downgrade() -> None:
    for column in ("created_at", "updated_at"):
        op.alter_column("seek_connections", column, server_default=None)
    op.drop_column("seek_connections", "project_hint")
