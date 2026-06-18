"""SQLAlchemy models for metaseed-hub."""

from datetime import datetime
from enum import StrEnum
from typing import Any
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .mixins import SoftDeleteMixin, TimestampMixin


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


class TeamRole(StrEnum):
    """Role within a team."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class SpecStatus(StrEnum):
    """Status of a published specification."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


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
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
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

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="users")
    memberships: Mapped[list["TeamMembership"]] = relationship(
        "TeamMembership", back_populates="user"
    )
    dataset_memberships: Mapped[list["DatasetMember"]] = relationship(
        "DatasetMember", back_populates="user"
    )
    spec_memberships: Mapped[list["SpecMember"]] = relationship("SpecMember", back_populates="user")
    notes: Mapped[list["Note"]] = relationship("Note", back_populates="user")
    chat_messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="user")
    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="user")
    comment_reactions: Mapped[list["CommentReaction"]] = relationship(
        "CommentReaction", back_populates="user"
    )
    spec_comments: Mapped[list["SpecComment"]] = relationship("SpecComment", back_populates="user")
    spec_comment_reactions: Mapped[list["SpecCommentReaction"]] = relationship(
        "SpecCommentReaction", back_populates="user"
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
        Enum(TeamRole),
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


class Dataset(TimestampMixin, SoftDeleteMixin, Base):
    """Dataset containing a single spec instance with entity data."""

    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_datasets_tenant_name"),
        Index("ix_datasets_tenant_id", "tenant_id"),
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
    spec_draft_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_drafts.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    profile: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="datasets")
    spec_draft: Mapped["SpecDraft | None"] = relationship("SpecDraft")
    notes: Mapped[list["Note"]] = relationship("Note", back_populates="dataset")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="dataset"
    )
    members: Mapped[list["DatasetMember"]] = relationship("DatasetMember", back_populates="dataset")
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="dataset", order_by="Comment.created_at"
    )
    versions: Mapped[list["DatasetVersion"]] = relationship(
        "DatasetVersion", back_populates="dataset", order_by="DatasetVersion.version_number.desc()"
    )


class DatasetVersion(TimestampMixin, Base):
    """Snapshot of dataset data at a point in time."""

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dataset_versions_number"),
        Index("ix_dataset_versions_dataset_id", "dataset_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="versions")
    created_by: Mapped["User | None"] = relationship("User")


class DatasetRole(StrEnum):
    """Role within a dataset."""

    OWNER = "owner"
    CURATOR = "curator"
    VIEWER = "viewer"


class DatasetMember(Base):
    """User membership in a dataset with role-based access."""

    __tablename__ = "dataset_members"
    __table_args__ = (
        Index("ix_dataset_members_dataset_id", "dataset_id"),
        Index("ix_dataset_members_user_id", "user_id"),
    )

    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[DatasetRole] = mapped_column(
        Enum(DatasetRole),
        nullable=False,
        default=DatasetRole.VIEWER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="dataset_memberships")


class Note(TimestampMixin, Base):
    """Notes attached to entities within a dataset."""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_dataset_entity", "dataset_id", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="notes")
    user: Mapped["User"] = relationship("User", back_populates="notes")


class ChatMessage(TimestampMixin, Base):
    """Real-time chat messages within a dataset."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_dataset_id", "dataset_id"),
        Index("ix_chat_messages_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="chat_messages")
    user: Mapped["User"] = relationship("User", back_populates="chat_messages")


class Comment(TimestampMixin, Base):
    """Threaded comments on datasets (Slack-style)."""

    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_dataset_id", "dataset_id"),
        Index("ix_comments_parent_id", "parent_id"),
        Index("ix_comments_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="comments")
    user: Mapped["User"] = relationship("User", back_populates="comments")
    parent: Mapped["Comment | None"] = relationship(
        "Comment", remote_side="Comment.id", back_populates="replies"
    )
    replies: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="parent", order_by="Comment.created_at"
    )
    reactions: Mapped[list["CommentReaction"]] = relationship(
        "CommentReaction", back_populates="comment", cascade="all, delete-orphan"
    )


class ReactionType(StrEnum):
    """Types of reactions on comments."""

    LIKE = "like"
    DISLIKE = "dislike"


class CommentReaction(Base):
    """User reactions (like/dislike) on comments."""

    __tablename__ = "comment_reactions"
    __table_args__ = (Index("ix_comment_reactions_comment_id", "comment_id"),)

    comment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("comments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reaction: Mapped[ReactionType] = mapped_column(
        Enum(
            ReactionType,
            name="reactiontype",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    comment: Mapped["Comment"] = relationship("Comment", back_populates="reactions")
    user: Mapped["User"] = relationship("User", back_populates="comment_reactions")


class SpecComment(TimestampMixin, Base):
    """Threaded comments on spec drafts."""

    __tablename__ = "spec_comments"
    __table_args__ = (
        Index("ix_spec_comments_spec_draft_id", "spec_draft_id"),
        Index("ix_spec_comments_parent_id", "parent_id"),
        Index("ix_spec_comments_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    spec_draft_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    spec_draft: Mapped["SpecDraft"] = relationship("SpecDraft", back_populates="comments")
    user: Mapped["User"] = relationship("User", back_populates="spec_comments")
    parent: Mapped["SpecComment | None"] = relationship(
        "SpecComment", remote_side="SpecComment.id", back_populates="replies"
    )
    replies: Mapped[list["SpecComment"]] = relationship(
        "SpecComment", back_populates="parent", order_by="SpecComment.created_at"
    )
    reactions: Mapped[list["SpecCommentReaction"]] = relationship(
        "SpecCommentReaction", back_populates="comment", cascade="all, delete-orphan"
    )


class SpecCommentReaction(Base):
    """User reactions (like/dislike) on spec comments."""

    __tablename__ = "spec_comment_reactions"
    __table_args__ = (Index("ix_spec_comment_reactions_comment_id", "comment_id"),)

    comment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_comments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reaction: Mapped[ReactionType] = mapped_column(
        Enum(
            ReactionType,
            name="reactiontype",
            create_type=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    comment: Mapped["SpecComment"] = relationship("SpecComment", back_populates="reactions")
    user: Mapped["User"] = relationship("User", back_populates="spec_comment_reactions")


class Spec(TimestampMixin, SoftDeleteMixin, Base):
    """Published specification that belongs to a tenant.

    Represents a finalized specification accessible to all team members
    within the tenant. Supports versioning for tracking changes.
    """

    __tablename__ = "specs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_specs_tenant_name_version"),
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
    status: Mapped[SpecStatus] = mapped_column(
        Enum(SpecStatus),
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


class SpecRole(StrEnum):
    """Role within a spec."""

    OWNER = "owner"
    CURATOR = "curator"
    VIEWER = "viewer"


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
    role: Mapped[SpecRole] = mapped_column(
        Enum(SpecRole),
        nullable=False,
        default=SpecRole.VIEWER,
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
    save progress and resume later. Users can have multiple drafts within
    a tenant, each with a unique name per user.
    """

    __tablename__ = "spec_drafts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "name", name="uq_spec_drafts_tenant_user_name"),
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
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="0.1")
    spec_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
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


class SpecDraftRole(StrEnum):
    """Role within a spec draft."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


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
    role: Mapped[SpecDraftRole] = mapped_column(
        Enum(SpecDraftRole),
        nullable=False,
        default=SpecDraftRole.VIEWER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    spec_draft: Mapped["SpecDraft"] = relationship("SpecDraft", back_populates="members")
    user: Mapped["User"] = relationship("User")


__all__ = [
    "Base",
    "ChatMessage",
    "Comment",
    "CommentReaction",
    "Dataset",
    "DatasetMember",
    "DatasetRole",
    "DatasetVersion",
    "Note",
    "ReactionType",
    "SoftDeleteMixin",
    "Spec",
    "SpecComment",
    "SpecCommentReaction",
    "SpecDraft",
    "SpecDraftMember",
    "SpecDraftRole",
    "SpecMember",
    "SpecRole",
    "SpecStatus",
    "Team",
    "TeamMembership",
    "TeamRole",
    "Tenant",
    "TimestampMixin",
    "User",
]
