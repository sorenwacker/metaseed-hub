"""a withdrawn spec must not keep reserving its name

Revision ID: 260728_spec_unpub
Revises: 260727_api_tokens
Create Date: 2026-07-28

``uq_specs_tenant_name_version`` is a plain unique constraint, so a soft-deleted
row still occupies the (tenant, name, version) slot. Unpublishing sets
``deleted_at``, and republishing the same specification afterwards would then
fail with an IntegrityError on a row nobody can see. A partial unique index
constrains only the live rows, which is what the rule always meant.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "260728_spec_unpub"
down_revision: str | None = "260727_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_specs_tenant_name_version", "specs", type_="unique")
    op.create_index(
        "uq_specs_tenant_name_version",
        "specs",
        ["tenant_id", "name", "version"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_specs_tenant_name_version", table_name="specs")
    op.create_unique_constraint(
        "uq_specs_tenant_name_version", "specs", ["tenant_id", "name", "version"]
    )
