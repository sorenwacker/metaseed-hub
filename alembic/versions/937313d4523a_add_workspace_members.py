"""add workspace members

Revision ID: 937313d4523a
Revises: d5b1a0f1f999
Create Date: 2026-05-06 22:40:38.132128

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "937313d4523a"
down_revision: str | None = "d5b1a0f1f999"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role",
            sa.Enum("owner", "editor", "viewer", name="workspacerole"),
            nullable=False,
            server_default="editor",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"])
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_members_user_id")
    op.drop_index("ix_workspace_members_workspace_id")
    op.drop_table("workspace_members")
    op.execute("DROP TYPE IF EXISTS workspacerole")
