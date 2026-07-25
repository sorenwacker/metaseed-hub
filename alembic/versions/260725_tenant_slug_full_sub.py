"""recompute tenant slugs from the full OIDC subject

Tenant slugs were the first 8 hex chars of the user's keycloak_id, a 32-bit
truncation that made the tenant isolation boundary collision-prone. Rewrite each
existing tenant's slug to the 128-bit SHA-256 derivation the code now uses, keyed
by the owning user's full keycloak_id (identified by the old truncated slug).

Revision ID: 260725_tenant_slug_full_sub
Revises: 260623_drop_chat_messages
Create Date: 2026-07-25

"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "260725_tenant_slug_full_sub"
down_revision: str | None = "260623_drop_chat_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _full_slug(keycloak_id: str) -> str:
    return hashlib.sha256(keycloak_id.encode()).hexdigest()[:32]


def _old_slug(keycloak_id: str) -> str:
    return keycloak_id[:8]


def upgrade() -> None:
    conn = op.get_bind()
    users = conn.execute(
        sa.text("SELECT keycloak_id, tenant_id FROM users ORDER BY created_at")
    ).fetchall()
    for keycloak_id, tenant_id in users:
        if not keycloak_id or tenant_id is None:
            continue
        # Only rewrite the owning tenant's truncated slug. The AND slug guard keeps
        # this idempotent and leaves a tenant already claimed by an earlier user
        # (an old collision) untouched rather than colliding on the unique slug.
        conn.execute(
            sa.text("UPDATE tenants SET slug = :new WHERE id = :tid AND slug = :old"),
            {"new": _full_slug(keycloak_id), "tid": tenant_id, "old": _old_slug(keycloak_id)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    users = conn.execute(
        sa.text("SELECT keycloak_id, tenant_id FROM users ORDER BY created_at")
    ).fetchall()
    for keycloak_id, tenant_id in users:
        if not keycloak_id or tenant_id is None:
            continue
        conn.execute(
            sa.text("UPDATE tenants SET slug = :old WHERE id = :tid AND slug = :new"),
            {"old": _old_slug(keycloak_id), "tid": tenant_id, "new": _full_slug(keycloak_id)},
        )
