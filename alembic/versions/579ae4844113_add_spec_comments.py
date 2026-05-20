"""add_spec_comments

Revision ID: 579ae4844113
Revises: 03d97af76817
Create Date: 2026-05-20 17:21:58.570305

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "579ae4844113"
down_revision: str | None = "03d97af76817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create spec_comments table
    op.create_table(
        "spec_comments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("spec_draft_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spec_draft_id"], ["spec_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["spec_comments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_spec_comments_spec_draft_id", "spec_comments", ["spec_draft_id"])
    op.create_index("ix_spec_comments_parent_id", "spec_comments", ["parent_id"])
    op.create_index("ix_spec_comments_created_at", "spec_comments", ["created_at"])

    # Create spec_comment_reactions table using raw SQL to reference existing enum
    op.execute("""
        CREATE TABLE spec_comment_reactions (
            comment_id UUID NOT NULL REFERENCES spec_comments(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reaction reactiontype NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            PRIMARY KEY (comment_id, user_id)
        )
    """)
    op.create_index(
        "ix_spec_comment_reactions_comment_id", "spec_comment_reactions", ["comment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_spec_comment_reactions_comment_id", table_name="spec_comment_reactions")
    op.drop_table("spec_comment_reactions")
    op.drop_index("ix_spec_comments_created_at", table_name="spec_comments")
    op.drop_index("ix_spec_comments_parent_id", table_name="spec_comments")
    op.drop_index("ix_spec_comments_spec_draft_id", table_name="spec_comments")
    op.drop_table("spec_comments")
