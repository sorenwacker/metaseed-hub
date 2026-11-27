"""Dataset versions carry a name, a note and the specification they were saved under.

A name and note mark a version that matters (submitted, reviewed); the spec
name and version record which specification the data was saved against. Older
versions keep NULL: what they were saved under is not known.

Revision ID: 261127_version_timeline
Revises: 261126_user_features
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "261127_version_timeline"
down_revision: str | None = "261126_user_features"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("dataset_versions", sa.Column("name", sa.String(200), nullable=True))
    op.add_column("dataset_versions", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("dataset_versions", sa.Column("spec_name", sa.String(255), nullable=True))
    op.add_column("dataset_versions", sa.Column("spec_version", sa.String(50), nullable=True))


def downgrade() -> None:
    for column in ("spec_version", "spec_name", "note", "name"):
        op.drop_column("dataset_versions", column)
