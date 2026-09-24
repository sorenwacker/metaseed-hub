"""Published specs, drafts and their membership."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from metaseed_hub.sharing import Role

from .base import Base, _enum_values

if TYPE_CHECKING:
    # Only for annotations: the real links are resolved by name through
    # SQLAlchemy's registry, so no module imports another at runtime and
    # there is nothing to form a cycle.
    from .comments import SpecComment
    from .identity import Tenant, User

from .mixins import SoftDeleteMixin, TimestampMixin


class SpecStatus(StrEnum):
    """Status of a published specification."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Spec(TimestampMixin, SoftDeleteMixin, Base):
    """Published specification that belongs to a tenant.

    Who may reach one is decided by :func:`metaseed_hub.sharing.role_of`:
    a membership, its creator, the one person whose account it lives in, or a
    collaboration grant. An account belongs to one person, so there is no team
    within a tenant to grant it to. Supports versioning for tracking changes.
    """

    __tablename__ = "specs"
    __table_args__ = (
        # Partial, so a withdrawn spec stops reserving its name: unpublishing
        # sets deleted_at, and republishing the same specification afterwards
        # must not collide with a row nobody can see.
        Index(
            "uq_specs_tenant_name_version",
            "tenant_id",
            "name",
            "version",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_specs_audience_urn", "audience_urn"),
        Index("ix_specs_tenant_id", "tenant_id"),
        Index("ix_specs_created_by_id", "created_by_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # "sha256:" plus 64 hex digits. Nullable because a row whose stored spec
    # cannot be deserialized has no hash to record, and pretending otherwise
    # would give it a name that identifies nothing.
    # NULL means every user of the hub. A SRAM collaboration or group URN
    # means only its members see it: the middle ground between a private
    # draft and a hub-wide release. Read through metaseed_hub.audience, never
    # compared directly, so one rule governs every listing.
    audience_urn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    status: Mapped[SpecStatus] = mapped_column(
        Enum(SpecStatus, values_callable=_enum_values),
        nullable=False,
        default=SpecStatus.PUBLISHED,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="specs")
    created_by: Mapped["User | None"] = relationship("User")
    drafts: Mapped[list["SpecDraft"]] = relationship("SpecDraft", back_populates="source_spec")
    members: Mapped[list["SpecMember"]] = relationship("SpecMember", back_populates="spec")


class SpecMember(Base):
    """User membership in a spec with role-based access."""

    __tablename__ = "spec_members"
    __table_args__ = (
        Index("ix_spec_members_spec_id", "spec_id"),
        Index("ix_spec_members_user_id", "user_id"),
    )

    spec_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("specs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="memberrole", values_callable=_enum_values),
        nullable=False,
        default=Role.VIEWER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    spec: Mapped["Spec"] = relationship("Spec", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="spec_memberships")


class SpecDraft(TimestampMixin, Base):
    """User-defined specification drafts in the spec builder.

    Stores the working state of a spec being built, allowing users to
    save progress and resume later. A draft is one name at one version
    within a user's account, so several versions of a name coexist as
    drafts, the way a specs directory holds ``<name>/<version>``.
    """

    __tablename__ = "spec_drafts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "name",
            "version",
            name="uq_spec_drafts_tenant_user_name_version",
        ),
        Index("ix_spec_drafts_tenant_id", "tenant_id"),
        Index("ix_spec_drafts_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_spec_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("specs.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # server_default mirrors the creating migration; declaring it keeps
    # `alembic check --compare-server-default` honest about model vs database.
    version: Mapped[str] = mapped_column(
        String(50), nullable=False, default="0.1", server_default="0.1"
    )
    spec_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    template_source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="spec_drafts")
    user: Mapped["User"] = relationship("User")
    source_spec: Mapped["Spec | None"] = relationship("Spec", back_populates="drafts")
    members: Mapped[list["SpecDraftMember"]] = relationship(
        "SpecDraftMember", back_populates="spec_draft", cascade="all, delete-orphan"
    )
    comments: Mapped[list["SpecComment"]] = relationship(
        "SpecComment",
        back_populates="spec_draft",
        order_by="SpecComment.created_at",
        cascade="all, delete-orphan",
    )


class SpecDraftMember(Base):
    """User membership in a spec draft with role-based access."""

    __tablename__ = "spec_draft_members"
    __table_args__ = (
        Index("ix_spec_draft_members_spec_draft_id", "spec_draft_id"),
        Index("ix_spec_draft_members_user_id", "user_id"),
    )

    spec_draft_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_drafts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="memberrole", values_callable=_enum_values),
        nullable=False,
        default=Role.VIEWER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    spec_draft: Mapped["SpecDraft"] = relationship("SpecDraft", back_populates="members")
    user: Mapped["User"] = relationship("User")
