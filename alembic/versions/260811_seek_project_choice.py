"""let a person choose which SEEK project content goes to

The push took `default_project_id()`, the first project the instance returned,
so anyone in more than one project had no say in where their records landed.
The chosen project is stored, along with the list as of the last successful
check so the choice can be offered without calling SEEK on every page load.

Revision ID: 260811_seek_project
Revises: 260810_one_role
Create Date: 2026-08-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "260811_seek_project"
down_revision: str | None = "260810_one_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("seek_connections", sa.Column("project_id", sa.String(50), nullable=True))
    op.add_column(
        "seek_connections",
        sa.Column("projects", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("seek_connections", "projects")
    op.drop_column("seek_connections", "project_id")
