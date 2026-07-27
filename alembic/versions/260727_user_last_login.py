"""record when a user last signed in

Revision ID: 260727_user_last_login
Revises: 260726_enum_store_by_value
Create Date: 2026-07-27

Nullable with no backfill: there is no record of past sign-ins to fill it from,
and inventing one (registration date, say) would misreport an account that has
never been used as having been used.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "260727_user_last_login"
down_revision: str | None = "260726_enum_store_by_value"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
