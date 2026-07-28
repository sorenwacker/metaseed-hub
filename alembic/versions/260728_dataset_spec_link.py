"""link a dataset to the published spec it was created from

Revision ID: 260728_ds_spec
Revises: 260728_spec_unpub
Create Date: 2026-07-28

``datasets.spec_draft_id`` only ever pointed at a *draft*. A dataset created
from a published specification had nowhere to record which one, so its facade
could not be rebuilt and the entity types would not resolve.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "260728_ds_spec"
down_revision: str | None = "260728_spec_unpub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("spec_id", UUID(as_uuid=False), nullable=True))
    # SET NULL rather than CASCADE: withdrawing a specification must not delete
    # the datasets built on it. They keep their stored contents.
    op.create_foreign_key(
        "fk_datasets_spec_id",
        "datasets",
        "specs",
        ["spec_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_datasets_spec_id", "datasets", type_="foreignkey")
    op.drop_column("datasets", "spec_id")
