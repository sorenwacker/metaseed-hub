"""A draft is one name at one version, not one name.

Drafts were unique per (tenant, user, name), so pushing the next version of a
profile replaced the draft of the previous one, and a person could not hold
1.2 and 1.3 of a profile as drafts at once although their specs directory
does exactly that. The key now includes the version.

Revision ID: 260922_drafts_per_version
Revises: 260828_dataset_creators_own
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op

revision: str = "260922_drafts_per_version"
down_revision: str | None = "260828_dataset_creators_own"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("uq_spec_drafts_tenant_user_name", "spec_drafts", type_="unique")
    op.create_unique_constraint(
        "uq_spec_drafts_tenant_user_name_version",
        "spec_drafts",
        ["tenant_id", "user_id", "name", "version"],
    )


def downgrade() -> None:
    # Fails while a user holds two versions of one name; those drafts have to
    # be renamed or removed first, which is a decision, not a migration.
    op.drop_constraint("uq_spec_drafts_tenant_user_name_version", "spec_drafts", type_="unique")
    op.create_unique_constraint(
        "uq_spec_drafts_tenant_user_name", "spec_drafts", ["tenant_id", "user_id", "name"]
    )
