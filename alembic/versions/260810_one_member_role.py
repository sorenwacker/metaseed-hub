"""one role vocabulary for every shared thing

A dataset had owners, curators and viewers; a specification draft had owners,
editors and viewers; a published specification had a members table nobody could
write to. Three enum types said almost the same thing, and the interface said
"the same roles" while meaning two different sets.

The three become one type, `memberrole`, with `curator` mapped to `editor` —
the word that names what the role does. Production held four membership rows
when this ran, one of them a curator.

Revision ID: 260810_one_role
Revises: 260810_drop_dead
Create Date: 2026-08-10

"""

from collections.abc import Sequence

from alembic import op

revision: str = "260810_one_role"
down_revision: str | None = "260810_drop_dead"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: table, column, and the enum type it used before.
MEMBERSHIPS = (
    ("dataset_members", "role", "datasetrole"),
    ("spec_members", "role", "specrole"),
    ("spec_draft_members", "role", "specdraftrole"),
)


def upgrade() -> None:
    op.execute("CREATE TYPE memberrole AS ENUM ('owner', 'editor', 'viewer')")
    for table, column, _old in MEMBERSHIPS:
        # Through text: 'curator' is not a member of the new type, so the value
        # has to be rewritten on the way across.
        op.execute(
            # No SET DEFAULT afterwards: the models default in Python, and a
            # database default they do not declare is exactly the divergence
            # tests/test_migrations_match_models.py exists to catch.
            f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT, "
            f"ALTER COLUMN {column} TYPE memberrole USING "
            f"CASE {column}::text WHEN 'curator' THEN 'editor' "
            f"ELSE {column}::text END::memberrole"
        )
    for old in {m[2] for m in MEMBERSHIPS}:
        op.execute(f"DROP TYPE IF EXISTS {old}")


def downgrade() -> None:
    op.execute("CREATE TYPE datasetrole AS ENUM ('owner', 'curator', 'viewer')")
    op.execute("CREATE TYPE specrole AS ENUM ('owner', 'curator', 'viewer')")
    op.execute("CREATE TYPE specdraftrole AS ENUM ('owner', 'editor', 'viewer')")
    for table, column, old in MEMBERSHIPS:
        editor_becomes = "'curator'" if old != "specdraftrole" else "'editor'"
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT, "
            f"ALTER COLUMN {column} TYPE {old} USING "
            f"CASE {column}::text WHEN 'editor' THEN {editor_becomes} "
            f"ELSE {column}::text END::{old}"
        )
    op.execute("DROP TYPE IF EXISTS memberrole")
