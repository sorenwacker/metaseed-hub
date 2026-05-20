"""rename projects to datasets and add member tables

Revision ID: 260520a
Revises: 0053220bed75
Create Date: 2026-05-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "260520a"
down_revision: str | None = "0053220bed75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Rename projects table to datasets
    op.rename_table("projects", "datasets")

    # Rename constraints and indexes for datasets table
    op.execute("ALTER INDEX ix_projects_workspace_id RENAME TO ix_datasets_workspace_id")
    op.execute(
        "ALTER TABLE datasets RENAME CONSTRAINT uq_projects_workspace_name "
        "TO uq_datasets_workspace_name"
    )

    # Rename project_id to dataset_id in notes table
    op.alter_column("notes", "project_id", new_column_name="dataset_id")
    op.drop_index("ix_notes_project_entity", table_name="notes")
    op.create_index("ix_notes_dataset_entity", "notes", ["dataset_id", "entity_type", "entity_id"])

    # Rename project_id to dataset_id in chat_messages table
    op.alter_column("chat_messages", "project_id", new_column_name="dataset_id")
    op.drop_index("ix_chat_messages_project_id", table_name="chat_messages")
    op.create_index("ix_chat_messages_dataset_id", "chat_messages", ["dataset_id"])

    # Create dataset_members table
    op.create_table(
        "dataset_members",
        sa.Column("dataset_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "role",
            sa.Enum("OWNER", "CURATOR", "VIEWER", name="datasetrole"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dataset_id", "user_id"),
    )
    op.create_index("ix_dataset_members_dataset_id", "dataset_members", ["dataset_id"])
    op.create_index("ix_dataset_members_user_id", "dataset_members", ["user_id"])

    # Create spec_members table
    op.create_table(
        "spec_members",
        sa.Column("spec_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "role",
            sa.Enum("OWNER", "CURATOR", "VIEWER", name="specrole"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spec_id"], ["specs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("spec_id", "user_id"),
    )
    op.create_index("ix_spec_members_spec_id", "spec_members", ["spec_id"])
    op.create_index("ix_spec_members_user_id", "spec_members", ["user_id"])


def downgrade() -> None:
    # Drop spec_members table
    op.drop_index("ix_spec_members_user_id", table_name="spec_members")
    op.drop_index("ix_spec_members_spec_id", table_name="spec_members")
    op.drop_table("spec_members")
    op.execute("DROP TYPE IF EXISTS specrole")

    # Drop dataset_members table
    op.drop_index("ix_dataset_members_user_id", table_name="dataset_members")
    op.drop_index("ix_dataset_members_dataset_id", table_name="dataset_members")
    op.drop_table("dataset_members")
    op.execute("DROP TYPE IF EXISTS datasetrole")

    # Rename dataset_id back to project_id in chat_messages
    op.drop_index("ix_chat_messages_dataset_id", table_name="chat_messages")
    op.alter_column("chat_messages", "dataset_id", new_column_name="project_id")
    op.create_index("ix_chat_messages_project_id", "chat_messages", ["project_id"])

    # Rename dataset_id back to project_id in notes
    op.drop_index("ix_notes_dataset_entity", table_name="notes")
    op.alter_column("notes", "dataset_id", new_column_name="project_id")
    op.create_index("ix_notes_project_entity", "notes", ["project_id", "entity_type", "entity_id"])

    # Rename constraints and indexes back
    op.execute(
        "ALTER TABLE datasets RENAME CONSTRAINT uq_datasets_workspace_name "
        "TO uq_projects_workspace_name"
    )
    op.execute("ALTER INDEX ix_datasets_workspace_id RENAME TO ix_projects_workspace_id")

    # Rename datasets table back to projects
    op.rename_table("datasets", "projects")
