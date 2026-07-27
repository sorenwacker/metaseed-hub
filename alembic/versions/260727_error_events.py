"""record unhandled server errors for the admin dashboard

Revision ID: 260727_error_events
Revises: 260727_user_last_login
Create Date: 2026-07-27

Chained onto the last-sign-in migration rather than onto their shared parent:
both were written the same day, and two revisions off one parent leaves alembic
with two heads and an upgrade that refuses to run. Merge that branch first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "260727_error_events"
down_revision: str | None = "260727_user_last_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "error_events",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("exception_type", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_error_events_occurred_at", "error_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_error_events_occurred_at", table_name="error_events")
    op.drop_table("error_events")
