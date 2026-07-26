"""store role/status enums by value, matching reactions

Role and status enum columns (teamrole, datasetrole, specrole, specdraftrole,
specstatus) stored the uppercase member *name* ("OWNER"), while reaction columns
stored the lowercase member *value* ("like"). This aligns the former to store by
value so every persisted enum is consistent.

The Postgres enum types store rows by an internal id, so renaming a label with
ALTER TYPE ... RENAME VALUE updates every existing row transparently; no per-row
UPDATE is needed. None of these columns has a server_default referencing a label,
so no default expression is affected.

Revision ID: 260726_enum_store_by_value
Revises: 260725_tenant_slug_full_sub
Create Date: 2026-07-26

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "260726_enum_store_by_value"
down_revision: str | None = "260725_tenant_slug_full_sub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# enum type name -> list of (uppercase name, lowercase value) label pairs.
# reactiontype already stores by value and is intentionally omitted.
_RENAMES: dict[str, list[tuple[str, str]]] = {
    "teamrole": [("OWNER", "owner"), ("ADMIN", "admin"), ("MEMBER", "member")],
    "datasetrole": [("OWNER", "owner"), ("CURATOR", "curator"), ("VIEWER", "viewer")],
    "specrole": [("OWNER", "owner"), ("CURATOR", "curator"), ("VIEWER", "viewer")],
    "specdraftrole": [("OWNER", "owner"), ("EDITOR", "editor"), ("VIEWER", "viewer")],
    "specstatus": [
        ("DRAFT", "draft"),
        ("PUBLISHED", "published"),
        ("ARCHIVED", "archived"),
    ],
}


def upgrade() -> None:
    for type_name, pairs in _RENAMES.items():
        for old, new in pairs:
            op.execute(f"ALTER TYPE {type_name} RENAME VALUE '{old}' TO '{new}'")


def downgrade() -> None:
    for type_name, pairs in _RENAMES.items():
        for old, new in pairs:
            op.execute(f"ALTER TYPE {type_name} RENAME VALUE '{new}' TO '{old}'")
