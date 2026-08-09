"""Who uses the hub: tenants, teams, users and membership."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _enum_values

if TYPE_CHECKING:
    # Only for annotations: the real links are resolved by name through
    # SQLAlchemy's registry, so no module imports another at runtime and
    # there is nothing to form a cycle.
    from .comments import Comment, CommentReaction, Note, SpecComment, SpecCommentReaction
    from .datasets import Dataset, DatasetMember
    from .operations import ApiToken
    from .specs import Spec, SpecDraft, SpecMember

from .mixins import SoftDeleteMixin, TimestampMixin


class TeamRole(StrEnum):
    """Role within a team."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Tenant(TimestampMixin, SoftDeleteMixin, Base):
    """Multi-tenant organization."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # Relationships
    teams: Mapped[list["Team"]] = relationship("Team", back_populates="tenant")
    users: Mapped[list["User"]] = relationship("User", back_populates="tenant")
    datasets: Mapped[list["Dataset"]] = relationship("Dataset", back_populates="tenant")
    specs: Mapped[list["Spec"]] = relationship("Spec", back_populates="tenant")
    spec_drafts: Mapped[list["SpecDraft"]] = relationship("SpecDraft", back_populates="tenant")


class Team(TimestampMixin, SoftDeleteMixin, Base):
    """Team within a tenant."""

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_teams_tenant_name"),
        Index("ix_teams_tenant_id", "tenant_id"),
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
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="teams")
    memberships: Mapped[list["TeamMembership"]] = relationship(
        "TeamMembership", back_populates="team"
    )


class User(TimestampMixin, SoftDeleteMixin, Base):
    """Application user linked to Keycloak."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "keycloak_id", name="uq_users_tenant_keycloak_id"),
        # Global, not per-tenant: sharing resolves an invitee by email across
        # tenants (every account has its own), so an address must identify
        # exactly one account for that lookup to be unambiguous. Addresses are
        # stored lowercased, which is what makes this constraint case-folding.
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    keycloak_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Written when the sign-in flow completes, not on every request, so this is
    # a last sign-in and not a last-seen. Null for users who registered before
    # the column existed and have not signed in since.
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")
    # passive_deletes: the child FKs are ON DELETE CASCADE, so let the database
    # remove these rows on user deletion rather than the ORM nulling their FKs
    # (which fails for membership/reaction tables where user_id is part of the
    # primary key). This is what makes account deletion possible.
    memberships: Mapped[list["TeamMembership"]] = relationship(
        "TeamMembership", back_populates="user", passive_deletes=True
    )
    dataset_memberships: Mapped[list["DatasetMember"]] = relationship(
        "DatasetMember", back_populates="user", passive_deletes=True
    )
    spec_memberships: Mapped[list["SpecMember"]] = relationship(
        "SpecMember", back_populates="user", passive_deletes=True
    )
    notes: Mapped[list["Note"]] = relationship("Note", back_populates="user", passive_deletes=True)
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="user", passive_deletes=True
    )
    comment_reactions: Mapped[list["CommentReaction"]] = relationship(
        "CommentReaction", back_populates="user", passive_deletes=True
    )
    spec_comments: Mapped[list["SpecComment"]] = relationship(
        "SpecComment", back_populates="user", passive_deletes=True
    )
    spec_comment_reactions: Mapped[list["SpecCommentReaction"]] = relationship(
        "SpecCommentReaction", back_populates="user", passive_deletes=True
    )
    api_tokens: Mapped[list["ApiToken"]] = relationship(
        "ApiToken", back_populates="user", passive_deletes=True
    )


class TeamMembership(Base):
    """Association between users and teams with roles."""

    __tablename__ = "team_memberships"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    team_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[TeamRole] = mapped_column(
        Enum(TeamRole, values_callable=_enum_values),
        nullable=False,
        default=TeamRole.MEMBER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="memberships")
    team: Mapped["Team"] = relationship("Team", back_populates="memberships")
