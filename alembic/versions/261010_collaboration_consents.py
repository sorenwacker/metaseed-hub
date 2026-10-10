"""Collaboration consents: a person's answer, yes or no, per collaboration.

Replaces the opt-outs of 0.64.0. The rows are not carried over: everyone
starts with the question open, which shows nothing and is asked.

Revision ID: 261010_collaboration_consents
Revises: 261008_collaboration_opt_outs
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "261010_collaboration_consents"
down_revision: str | None = "261008_collaboration_opt_outs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "collaboration_consents",
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("urn", sa.String(length=512), nullable=False),
        sa.Column("shown", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "urn"),
    )
    op.drop_table("collaboration_opt_outs")


def downgrade() -> None:
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
    op.drop_table("collaboration_consents")
