"""let a personal access token expire

Revision ID: 260728_tok_exp
Revises: 260728_ds_spec
Create Date: 2026-07-28

A token was valid until explicitly revoked. One pasted into a config file and
forgotten stayed a working credential indefinitely; an expiry bounds that
without requiring anyone to remember.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "260728_tok_exp"
down_revision: str | None = "260728_ds_spec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, so tokens issued before this stay valid: silently expiring
    # someone's working credential is worse than the unbounded lifetime.
    op.add_column(
        "api_tokens",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_tokens", "expires_at")
