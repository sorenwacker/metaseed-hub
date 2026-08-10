"""Operational records: feature grants, API tokens, error events."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only for annotations: the real links are resolved by name through
    # SQLAlchemy's registry, so no module imports another at runtime and
    # there is nothing to form a cycle.
    from .identity import User

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .mixins import TimestampMixin


class SeekConnection(TimestampMixin, Base):
    """One user's connection to a FAIRDOM-SEEK instance.

    Per tenant — and hub tenants are per user — because SEEK creates every
    record as the API key's person, so sharing a key would publish one
    person's name on everyone's work. The key is encrypted at rest
    (:mod:`metaseed_hub.crypto`) and never rendered back into a page.
    """

    __tablename__ = "seek_connections"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id"), unique=True, nullable=False
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # A connection is stored whether or not it works: refusing to save what the
    # user just typed means retyping the API key to fix a typo in the URL. The
    # status is kept alongside instead, and shown wherever the connection is.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeatureGrant(TimestampMixin, Base):
    """A feature made available to everyone in one identity-provider group.

    Group membership itself is never stored -- it comes from the IdP on every
    login (see :mod:`metaseed_hub.entitlements`). This table holds only the
    policy the IdP cannot express: which feature a group may use.

    There is deliberately no user foreign key. A person is entitled because of a
    group they are in, so "only me" is a group of one rather than a second
    mechanism that would then need its own UI, tests and audit trail.

    ``group_urn`` matches either a group or a whole collaboration, since
    :func:`metaseed_hub.entitlements.entitled_urns` offers both for a user and
    they are compared by plain equality.
    """

    __tablename__ = "feature_grants"
    __table_args__ = (
        # A grant is a fact, not a quantity: granting twice must not mean twice.
        UniqueConstraint("feature", "group_urn", name="uq_feature_grants_feature_group"),
        Index("ix_feature_grants_group_urn", "group_urn"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    feature: Mapped[str] = mapped_column(String(100), nullable=False)
    group_urn: Mapped[str] = mapped_column(String(512), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ApiToken(Base):
    """A personal access token, for clients that cannot hold a browser session.

    Issued to a user so an MCP client can act as them. Only the SHA-256 hash is
    stored: the token itself is shown once at creation and is unrecoverable
    afterwards, so a database copy cannot be replayed against the API.

    Revocation is a timestamp rather than a delete, so an admin can still see
    that a token existed and when it was withdrawn.
    """

    __tablename__ = "api_tokens"
    __table_args__ = (Index("ix_api_tokens_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    """What the user called it, so they can tell two tokens apart."""
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    """SHA-256 of the token. Unique so a lookup is one indexed read."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """Updated on use, so a token nobody uses is visible as such."""
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the token stops working on its own.

    Nullable: tokens issued before expiry existed stay valid, because silently
    killing someone's working credential is worse than the unbounded lifetime.
    """

    user: Mapped["User"] = relationship("User", back_populates="api_tokens")

    @property
    def is_active(self) -> bool:
        """Whether this token may still authenticate.

        Expiry is checked here rather than in the query, so every caller gets
        the same answer and none can forget it.
        """
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= datetime.now(UTC):
            return False
        return True


class ErrorEvent(Base):
    """An unhandled server error, recorded so admins can see it in the hub.

    Stored in the database rather than read from the journal: the app runs
    several uvicorn workers, each logging separately, and the admin dashboard
    has no privilege to read the host's journal. A row per error also survives
    restarts and is queryable.

    Only the exception type and its message are kept — never the request body or
    headers — so a stack of these does not become a copy of users' data.
    """

    __tablename__ = "error_events"
    __table_args__ = (Index("ix_error_events_occurred_at", "occurred_at"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    exception_type: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Null when the error happened before the caller was identified, or after
    # the account was deleted.
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped["User | None"] = relationship("User")
