"""add spec_drafts table

Revision ID: 35d128585feb
Revises: 001
Create Date: 2026-04-29 11:47:38.467289

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "35d128585feb"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Check if table already exists
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)
    if "spec_drafts" not in inspector.get_table_names():
        op.create_table(
            "spec_drafts",
            sa.Column("id", UUID(as_uuid=False), primary_key=True),
            sa.Column(
                "tenant_id",
                UUID(as_uuid=False),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                UUID(as_uuid=False),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("version", sa.String(50), nullable=False, server_default="0.1"),
            sa.Column("spec_data", JSONB, nullable=False, server_default="{}"),
            sa.Column("template_source", sa.String(255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_unique_constraint(
            "uq_spec_drafts_tenant_user", "spec_drafts", ["tenant_id", "user_id"]
        )
        op.create_index("ix_spec_drafts_tenant_id", "spec_drafts", ["tenant_id"])


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_spec_drafts_tenant_id")
    op.execute("ALTER TABLE spec_drafts DROP CONSTRAINT IF EXISTS uq_spec_drafts_tenant_user")
    op.execute("DROP TABLE IF EXISTS spec_drafts")
