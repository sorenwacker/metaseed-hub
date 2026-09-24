"""Record when a person's membership was read, not only what it said.

An empty reading has no rows, so it was indistinguishable from never having
asked -- and the profile page told a person whose session predated the feature
that their identity provider had reported no collaboration, which it never
had. The time moves onto the user, where an empty reading can carry it.

Revision ID: 260924_membership_read_at
Revises: 260922_collaborations
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "260924_membership_read_at"
down_revision: str | None = "260922_collaborations"
branch_labels: str | None = None
depends_on: str | None = None

#: Whatever was already recorded was read at the time of its newest row.
BACKFILL = """
UPDATE users SET memberships_read_at = newest.seen_at
FROM (
    SELECT user_id, MAX(seen_at) AS seen_at FROM group_memberships GROUP BY user_id
) AS newest
WHERE users.id = newest.user_id
"""


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("memberships_read_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(sa.text(BACKFILL))
    op.drop_column("group_memberships", "seen_at")


def downgrade() -> None:
    op.add_column(
        "group_memberships",
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE group_memberships SET seen_at = users.memberships_read_at "
            "FROM users WHERE users.id = group_memberships.user_id "
            "AND users.memberships_read_at IS NOT NULL"
        )
    )
    op.drop_column("users", "memberships_read_at")
