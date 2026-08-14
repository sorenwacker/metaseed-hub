"""Drop the feature_grants table: features were never grantable.

The table held which identity-provider group may use which optional feature,
but no interface ever wrote a row — so everything gated on it (the SEEK
panel, the DCAT column, the adapter export menu) was hidden from every user
since it shipped. Adapters are plugins available to every signed-in user,
like the metaseed UI offers them, and the mechanism is gone with its gate.

Revision ID: 260814_drop_feature_grants
Revises: 260811_seek_project
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "260814_drop_feature_grants"
down_revision: str | None = "260811_seek_project"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("ix_feature_grants_group_urn", table_name="feature_grants")
    op.drop_table("feature_grants")


def downgrade() -> None:
    # Mirrors 260808_feature_grants exactly, so a downgrade lands the schema
    # that migration created.
    op.create_table(
        "feature_grants",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("feature", sa.String(length=100), nullable=False),
        sa.Column("group_urn", sa.String(length=512), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature", "group_urn", name="uq_feature_grants_feature_group"),
    )
    op.create_index("ix_feature_grants_group_urn", "feature_grants", ["group_urn"], unique=False)
