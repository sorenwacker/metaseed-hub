"""Record SRAM group membership per user, and let a collaboration hold a role.

The identity provider states membership at sign-in and nowhere else, and an
access token carries none of it, so the hub keeps a per-user snapshot of the
last statement. The grant tables give a collaboration, or one of its groups,
the editor or viewer role on a dataset, a draft or a published specification;
one table per kind so the database drops a grant with the thing it is on.

Revision ID: 260922_collaborations
Revises: 260922_drafts_per_version
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "260922_collaborations"
down_revision: str | None = "260922_drafts_per_version"
branch_labels: str | None = None
depends_on: str | None = None

#: The role enum the member tables already use; never created here.
MEMBER_ROLE = postgresql.ENUM("owner", "editor", "viewer", name="memberrole", create_type=False)

GRANT_TABLES = (
    ("dataset_collaboration_grants", "dataset_id", "datasets"),
    ("spec_draft_collaboration_grants", "spec_draft_id", "spec_drafts"),
    ("spec_collaboration_grants", "spec_id", "specs"),
)


def upgrade() -> None:
    op.create_table(
        "group_memberships",
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("urn", sa.String(length=512), primary_key=True),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_group_memberships_urn", "group_memberships", ["urn"])
    for table, column, parent in GRANT_TABLES:
        op.create_table(
            table,
            sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
            sa.Column(
                column,
                sa.UUID(as_uuid=False),
                sa.ForeignKey(f"{parent}.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("urn", sa.String(length=512), nullable=False),
            sa.Column("role", MEMBER_ROLE, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(column, "urn", name=f"uq_{table}"),
        )


def downgrade() -> None:
    for table, _column, _parent in GRANT_TABLES:
        op.drop_table(table)
    op.drop_index("ix_group_memberships_urn", table_name="group_memberships")
    op.drop_table("group_memberships")
