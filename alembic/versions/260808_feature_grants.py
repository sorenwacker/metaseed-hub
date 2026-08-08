"""add feature grants

Holds the entitlement policy an identity provider cannot express: which feature
a group may use. Group membership itself is not stored -- it arrives in the
token on every login.

Revision ID: 260808_feature_grants
Revises: 260803_email_unique
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "260808_feature_grants"
down_revision: str | None = "260803_email_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_grants",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("feature", sa.String(length=100), nullable=False),
        # Long enough for a SRAM group URN, which nests organisation,
        # collaboration and group under a fixed prefix.
        sa.Column("group_urn", sa.String(length=512), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # A grant is a fact, not a quantity: granting twice must not mean twice.
        sa.UniqueConstraint("feature", "group_urn", name="uq_feature_grants_feature_group"),
    )
    # Every request resolves a user's features by their group URNs, so this is
    # the read path rather than an afterthought.
    op.create_index("ix_feature_grants_group_urn", "feature_grants", ["group_urn"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feature_grants_group_urn", table_name="feature_grants")
    op.drop_table("feature_grants")
