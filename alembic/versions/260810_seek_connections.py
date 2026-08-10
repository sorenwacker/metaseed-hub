"""add seek connections

One per tenant: the user's FAIRDOM-SEEK instance URL and API key, the key
encrypted at rest. SEEK creates every record as the key's person, which is why
the connection is per user and never shared.

Revision ID: 260810_seek_connections
Revises: 260808_feature_grants
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "260810_seek_connections"
down_revision: str | None = "260808_feature_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seek_connections",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", name="uq_seek_connections_tenant"),
    )


def downgrade() -> None:
    op.drop_table("seek_connections")
