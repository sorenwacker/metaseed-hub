"""add_spec_draft_members

Revision ID: 47ff3ffbc8a8
Revises: 260520a
Create Date: 2026-05-20 16:51:24.258396

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "47ff3ffbc8a8"
down_revision: str | None = "260520a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spec_draft_members",
        sa.Column("spec_draft_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role", sa.Enum("OWNER", "EDITOR", "VIEWER", name="specdraftrole"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spec_draft_id"], ["spec_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("spec_draft_id", "user_id"),
    )
    op.create_index("ix_spec_draft_members_spec_draft_id", "spec_draft_members", ["spec_draft_id"])
    op.create_index("ix_spec_draft_members_user_id", "spec_draft_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_spec_draft_members_user_id", table_name="spec_draft_members")
    op.drop_index("ix_spec_draft_members_spec_draft_id", table_name="spec_draft_members")
    op.drop_table("spec_draft_members")
    op.execute("DROP TYPE IF EXISTS specdraftrole")
