"""Collaboration opt-outs: a person's choice to keep their name and address from one collaboration.

Revision ID: 261008_collaboration_opt_outs
Revises: 261008_import_jobs
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "261008_collaboration_opt_outs"
down_revision: str | None = "261008_import_jobs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "collaboration_opt_outs",
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("urn", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "urn"),
    )


def downgrade() -> None:
    op.drop_table("collaboration_opt_outs")
