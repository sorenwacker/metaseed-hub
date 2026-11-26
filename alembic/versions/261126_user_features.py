"""Repository integrations are switched on per user, not per SRAM group.

Adds ``user_features``: one row per integration a user switched on. Nobody has
a row yet, so every integration starts off for every user, including those
whose SRAM groups granted it before.

Revision ID: 261126_user_features
Revises: 261030_published_spec_comments
Create Date: 2026-11-26
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "261126_user_features"
down_revision: str | None = "261030_published_spec_comments"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "user_features",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("feature", sa.String(50), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("user_features")
