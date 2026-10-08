"""Import jobs: repository imports that run in the background.

Revision ID: 261008_import_jobs
Revises: 261005_notifications
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "261008_import_jobs"
down_revision: str | None = "261005_notifications"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("profile", sa.String(length=100), nullable=False),
        sa.Column("identifiers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcomes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_jobs_user_id_created_at", "import_jobs", ["user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs_user_id_created_at", table_name="import_jobs")
    op.drop_table("import_jobs")
