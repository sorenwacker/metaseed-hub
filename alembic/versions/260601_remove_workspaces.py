"""Remove workspace abstraction - datasets belong directly to tenants.

Revision ID: 260601_remove_workspaces
Revises: 7ad5be33dc59
Create Date: 2026-06-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "260601_remove_workspaces"
down_revision: str | None = "7ad5be33dc59"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove workspace layer - datasets, specs, spec_drafts belong to tenants."""
    # Step 1: Add tenant_id columns to datasets, specs, spec_drafts
    op.add_column(
        "datasets",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "specs",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "spec_drafts",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=True),
    )

    # Step 2: Migrate data - set tenant_id from workspace.tenant_id
    op.execute("""
        UPDATE datasets
        SET tenant_id = workspaces.tenant_id
        FROM workspaces
        WHERE datasets.workspace_id = workspaces.id
    """)
    op.execute("""
        UPDATE specs
        SET tenant_id = workspaces.tenant_id
        FROM workspaces
        WHERE specs.workspace_id = workspaces.id
    """)
    op.execute("""
        UPDATE spec_drafts
        SET tenant_id = workspaces.tenant_id
        FROM workspaces
        WHERE spec_drafts.workspace_id = workspaces.id
    """)

    # Step 3: Make tenant_id NOT NULL after migration
    op.alter_column("datasets", "tenant_id", nullable=False)
    op.alter_column("specs", "tenant_id", nullable=False)
    op.alter_column("spec_drafts", "tenant_id", nullable=False)

    # Step 4: Add foreign key constraints for tenant_id
    op.create_foreign_key(
        "fk_datasets_tenant_id",
        "datasets",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_specs_tenant_id",
        "specs",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_spec_drafts_tenant_id",
        "spec_drafts",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Step 5: Create new indexes on tenant_id
    op.create_index("ix_datasets_tenant_id", "datasets", ["tenant_id"])
    op.create_index("ix_specs_tenant_id", "specs", ["tenant_id"])
    op.create_index("ix_spec_drafts_tenant_id", "spec_drafts", ["tenant_id"])

    # Step 6: Update unique constraints to use tenant_id instead of workspace_id
    # datasets: drop old unique constraint and create new one
    op.drop_constraint("uq_datasets_workspace_name", "datasets", type_="unique")
    op.create_unique_constraint(
        "uq_datasets_tenant_name",
        "datasets",
        ["tenant_id", "name"],
    )

    # specs: drop old unique constraint and create new one
    op.drop_constraint("uq_specs_workspace_name_version", "specs", type_="unique")
    op.create_unique_constraint(
        "uq_specs_tenant_name_version",
        "specs",
        ["tenant_id", "name", "version"],
    )

    # spec_drafts: drop old unique constraint and create new one
    op.drop_constraint("uq_spec_drafts_workspace_user_name", "spec_drafts", type_="unique")
    op.create_unique_constraint(
        "uq_spec_drafts_tenant_user_name",
        "spec_drafts",
        ["tenant_id", "user_id", "name"],
    )

    # Step 7: Drop workspace_id foreign keys and columns
    # Note: datasets FK is named projects_workspace_id_fkey from when table was called projects
    op.drop_constraint("projects_workspace_id_fkey", "datasets", type_="foreignkey")
    op.drop_index("ix_datasets_workspace_id", table_name="datasets")
    op.drop_column("datasets", "workspace_id")

    op.drop_constraint("specs_workspace_id_fkey", "specs", type_="foreignkey")
    op.drop_index("ix_specs_workspace_id", table_name="specs")
    op.drop_column("specs", "workspace_id")

    op.drop_constraint("spec_drafts_workspace_id_fkey", "spec_drafts", type_="foreignkey")
    op.drop_index("ix_spec_drafts_workspace_id", table_name="spec_drafts")
    op.drop_column("spec_drafts", "workspace_id")

    # Step 8: Drop workspace-related tables
    op.drop_table("workspace_teams")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")

    # Drop workspace role enum type
    op.execute("DROP TYPE IF EXISTS workspacerole")


def downgrade() -> None:
    """Restore workspace abstraction."""
    # Re-create workspaces table
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_workspaces_tenant_name"),
    )
    op.create_index("ix_workspaces_tenant_id", "workspaces", ["tenant_id"])

    # Re-create workspace_members table
    op.execute("CREATE TYPE workspacerole AS ENUM ('OWNER', 'EDITOR', 'VIEWER')")
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "role",
            sa.Enum("OWNER", "EDITOR", "VIEWER", name="workspacerole"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index("ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"])
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    # Re-create workspace_teams table
    op.create_table(
        "workspace_teams",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "team_id"),
    )
    op.create_index("ix_workspace_teams_workspace_id", "workspace_teams", ["workspace_id"])
    op.create_index("ix_workspace_teams_team_id", "workspace_teams", ["team_id"])

    # Add workspace_id columns back
    op.add_column(
        "datasets",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "specs",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "spec_drafts",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=False), nullable=True),
    )

    # Create default workspace for each tenant and migrate data back
    op.execute("""
        INSERT INTO workspaces (id, tenant_id, name)
        SELECT gen_random_uuid(), id, 'Default'
        FROM tenants
    """)

    op.execute("""
        UPDATE datasets
        SET workspace_id = workspaces.id
        FROM workspaces
        WHERE datasets.tenant_id = workspaces.tenant_id
    """)
    op.execute("""
        UPDATE specs
        SET workspace_id = workspaces.id
        FROM workspaces
        WHERE specs.tenant_id = workspaces.tenant_id
    """)
    op.execute("""
        UPDATE spec_drafts
        SET workspace_id = workspaces.id
        FROM workspaces
        WHERE spec_drafts.tenant_id = workspaces.tenant_id
    """)

    # Make workspace_id NOT NULL
    op.alter_column("datasets", "workspace_id", nullable=False)
    op.alter_column("specs", "workspace_id", nullable=False)
    op.alter_column("spec_drafts", "workspace_id", nullable=False)

    # Add foreign keys and indexes
    # Note: datasets FK uses projects_ prefix from old table name
    op.create_foreign_key(
        "projects_workspace_id_fkey",
        "datasets",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_datasets_workspace_id", "datasets", ["workspace_id"])

    op.create_foreign_key(
        "specs_workspace_id_fkey",
        "specs",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_specs_workspace_id", "specs", ["workspace_id"])

    op.create_foreign_key(
        "spec_drafts_workspace_id_fkey",
        "spec_drafts",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_spec_drafts_workspace_id", "spec_drafts", ["workspace_id"])

    # Restore unique constraints
    op.drop_constraint("uq_datasets_tenant_name", "datasets", type_="unique")
    op.create_unique_constraint(
        "uq_datasets_workspace_name",
        "datasets",
        ["workspace_id", "name"],
    )

    op.drop_constraint("uq_specs_tenant_name_version", "specs", type_="unique")
    op.create_unique_constraint(
        "uq_specs_workspace_name_version",
        "specs",
        ["workspace_id", "name", "version"],
    )

    op.drop_constraint("uq_spec_drafts_tenant_user_name", "spec_drafts", type_="unique")
    op.create_unique_constraint(
        "uq_spec_drafts_workspace_user_name",
        "spec_drafts",
        ["workspace_id", "user_id", "name"],
    )

    # Drop tenant_id columns and constraints
    op.drop_constraint("fk_datasets_tenant_id", "datasets", type_="foreignkey")
    op.drop_index("ix_datasets_tenant_id", table_name="datasets")
    op.drop_column("datasets", "tenant_id")

    op.drop_constraint("fk_specs_tenant_id", "specs", type_="foreignkey")
    op.drop_index("ix_specs_tenant_id", table_name="specs")
    op.drop_column("specs", "tenant_id")

    op.drop_constraint("fk_spec_drafts_tenant_id", "spec_drafts", type_="foreignkey")
    op.drop_index("ix_spec_drafts_tenant_id", table_name="spec_drafts")
    op.drop_column("spec_drafts", "tenant_id")
