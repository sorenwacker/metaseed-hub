"""Give every dataset an owner: the user of the account it lives in.

Nothing wrote a membership row when a dataset was created, and the sharing
rules read ownership from that table alone, so a dataset's creator had no
role in it: the sharing panel showed them no controls and refused their
shares. Creation now records the creator as owner; this migration does the
same for every dataset that predates it, choosing the account's oldest live
user, which is the one person an account has.

Revision ID: 260828_dataset_creators_own
Revises: 260814_drop_feature_grants
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "260828_dataset_creators_own"
down_revision: str | None = "260814_drop_feature_grants"
branch_labels: str | None = None
depends_on: str | None = None

#: One owner row per dataset that has none, for the account's oldest live user.
BACKFILL_OWNERS = """
INSERT INTO dataset_members (dataset_id, user_id, role)
SELECT DISTINCT ON (d.id) d.id, u.id, CAST('owner' AS memberrole)
FROM datasets d
JOIN users u ON u.tenant_id = d.tenant_id AND u.deleted_at IS NULL
WHERE NOT EXISTS (
    SELECT 1 FROM dataset_members m
    WHERE m.dataset_id = d.id AND m.role = CAST('owner' AS memberrole)
)
ORDER BY d.id, u.created_at
"""


def upgrade() -> None:
    op.execute(sa.text(BACKFILL_OWNERS))


def downgrade() -> None:
    # The rows are indistinguishable from owners added by hand, and removing
    # owners would orphan datasets; a downgrade keeps them.
    pass
