"""Drop the unused chat_messages table.

The chat feature was never completed: messages were neither persisted nor
broadcast, so the table held no data. Removing the table and the dead model.

Revision ID: 260623_drop_chat_messages
Revises: 260601_remove_workspaces
Create Date: 2026-06-23

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "260623_drop_chat_messages"
down_revision: str | None = "260601_remove_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the chat_messages table and its indexes."""
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_dataset_id", table_name="chat_messages")
    op.drop_table("chat_messages")


def downgrade() -> None:
    """Recreate the chat_messages table."""
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_dataset_id", "chat_messages", ["dataset_id"])
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])
