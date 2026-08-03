"""store user emails lowercased and make the address globally unique

Sharing resolves an invitee by email. Every account has its own tenant (the slug
is a SHA-256 of the OIDC subject), so an invitee is always outside the sharer's
tenant and a tenant-scoped lookup matched nobody. Making the lookup unscoped
requires the address to identify exactly one account, which per-tenant
uniqueness did not guarantee.

Lowercase first, then swap uq_users_tenant_email for a global uq_users_email.
Case-folding can merge two addresses that differed only in capitalisation; that
would silently hand one person's invitations to another, so this refuses to run
rather than picking a winner. Production held no such pair when this was written
(19 accounts, 2 not lowercased, 0 collisions).

Revision ID: 260803_email_unique
Revises: 260731_spec_hash
Create Date: 2026-08-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "260803_email_unique"
down_revision: str | None = "260731_spec_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    collisions = conn.execute(
        sa.text(
            "SELECT lower(email) AS addr, count(*) AS n FROM users "
            "GROUP BY lower(email) HAVING count(*) > 1 ORDER BY addr"
        )
    ).fetchall()
    if collisions:
        listed = ", ".join(f"{addr} ({n} accounts)" for addr, n in collisions)
        raise RuntimeError(
            "Cannot enforce one account per email: these addresses are held by "
            f"more than one account once case is folded: {listed}. Merge or "
            "remove the duplicate accounts, then run this migration again."
        )

    conn.execute(sa.text("UPDATE users SET email = lower(email) WHERE email <> lower(email)"))

    op.drop_constraint("uq_users_tenant_email", "users", type_="unique")
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    # Lowercasing is not reversed: the original casing is not recorded anywhere,
    # and the per-tenant constraint is satisfied by the lowercased values.
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.create_unique_constraint("uq_users_tenant_email", "users", ["tenant_id", "email"])
