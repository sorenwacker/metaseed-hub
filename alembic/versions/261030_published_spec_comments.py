"""Comments on a published specification, beside those on drafts.

A comment now names either a draft or a published specification, never both.
Existing rows all name a draft, so they satisfy the new constraint unchanged.

Revision ID: 261030_published_spec_comments
Revises: 261010_collaboration_consents
Create Date: 2026-10-30
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "261030_published_spec_comments"
down_revision: str | None = "261010_collaboration_consents"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("spec_comments", sa.Column("spec_id", postgresql.UUID(as_uuid=False)))
    op.create_foreign_key(
        "spec_comments_spec_id_fkey",
        "spec_comments",
        "specs",
        ["spec_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_spec_comments_spec_id", "spec_comments", ["spec_id"])
    op.alter_column("spec_comments", "spec_draft_id", nullable=True)
    op.create_check_constraint(
        "ck_spec_comments_one_subject", "spec_comments", "num_nonnulls(spec_draft_id, spec_id) = 1"
    )


def downgrade() -> None:
    op.execute("DELETE FROM spec_comments WHERE spec_id IS NOT NULL")
    op.drop_constraint("ck_spec_comments_one_subject", "spec_comments", type_="check")
    op.alter_column("spec_comments", "spec_draft_id", nullable=False)
    op.drop_index("ix_spec_comments_spec_id", table_name="spec_comments")
    op.drop_constraint("spec_comments_spec_id_fkey", "spec_comments", type_="foreignkey")
    op.drop_column("spec_comments", "spec_id")
