"""Give a published specification an audience.

Publishing meant visible to every user of the hub. A specification now carries
the collaboration it was published to, or NULL for everyone, which is what
every existing row is: they were all published hub-wide, so a NULL default
preserves exactly what people can see today.

Revision ID: 260924_spec_audience
Revises: 260924_membership_read_at
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "260924_spec_audience"
down_revision: str | None = "260924_membership_read_at"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("specs", sa.Column("audience_urn", sa.String(length=512), nullable=True))
    # Every listing filters on it, so this is the read path.
    op.create_index("ix_specs_audience_urn", "specs", ["audience_urn"])


def downgrade() -> None:
    # Narrowed specifications become hub-wide again, which widens what people
    # can see. That is the only way back, and it is why this is recorded here.
    op.drop_index("ix_specs_audience_urn", table_name="specs")
    op.drop_column("specs", "audience_urn")
