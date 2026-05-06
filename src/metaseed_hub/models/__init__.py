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
    workspaces: Mapped[list["Workspace"]] = relationship("Workspace", back_populates="tenant")


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
    workspaces: Mapped[list["WorkspaceTeam"]] = relationship("WorkspaceTeam", back_populates="team")


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
    workspace_memberships: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember", back_populates="user"
    )
    notes: Mapped[list["Note"]] = relationship("Note", back_populates="user")
    chat_messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="user")


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


class WorkspaceTeam(Base):
    """Association between workspaces and teams for access control.

    Links workspaces to teams, enabling team-based access to workspace resources.
    A workspace can be accessible to multiple teams, and a team can access
    multiple workspaces.
    """

    __tablename__ = "workspace_teams"
    __table_args__ = (
        Index("ix_workspace_teams_workspace_id", "workspace_id"),
        Index("ix_workspace_teams_team_id", "team_id"),
    )

    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    team_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="teams")
    team: Mapped["Team"] = relationship("Team", back_populates="workspaces")


class WorkspaceRole(StrEnum):
    """Role within a workspace."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class WorkspaceMember(Base):
    """Direct user membership in a workspace."""

    __tablename__ = "workspace_members"
    __table_args__ = (
        Index("ix_workspace_members_workspace_id", "workspace_id"),
        Index("ix_workspace_members_user_id", "user_id"),
    )

    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole),
        nullable=False,
        default=WorkspaceRole.EDITOR,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="workspace_memberships")


class Workspace(TimestampMixin, SoftDeleteMixin, Base):
    """Workspace for organizing projects."""

    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_workspaces_tenant_name"),
        Index("ix_workspaces_tenant_id", "tenant_id"),
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
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="workspaces")
    projects: Mapped[list["Project"]] = relationship("Project", back_populates="workspace")
    teams: Mapped[list["WorkspaceTeam"]] = relationship("WorkspaceTeam", back_populates="workspace")
    members: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember", back_populates="workspace"
    )
    specs: Mapped[list["Spec"]] = relationship("Spec", back_populates="workspace")
    spec_drafts: Mapped[list["SpecDraft"]] = relationship("SpecDraft", back_populates="workspace")


class Project(TimestampMixin, SoftDeleteMixin, Base):
    """Project containing metaseed data."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_projects_workspace_name"),
        Index("ix_projects_workspace_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
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
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="projects")
    spec_draft: Mapped["SpecDraft | None"] = relationship("SpecDraft")
    notes: Mapped[list["Note"]] = relationship("Note", back_populates="project")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="project"
    )


class Note(TimestampMixin, Base):
    """Notes attached to entities within a project."""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_project_entity", "project_id", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("projects.id", ondelete="CASCADE"),
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
    project: Mapped["Project"] = relationship("Project", back_populates="notes")
    user: Mapped["User"] = relationship("User", back_populates="notes")


class ChatMessage(TimestampMixin, Base):
    """Real-time chat messages within a project."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_project_id", "project_id"),
        Index("ix_chat_messages_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="chat_messages")
    user: Mapped["User"] = relationship("User", back_populates="chat_messages")


class Spec(TimestampMixin, SoftDeleteMixin, Base):
    """Published specification that belongs to a workspace.

    Represents a finalized specification accessible to all team members
    with access to the workspace. Supports versioning for tracking changes.
    """

    __tablename__ = "specs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", "version", name="uq_specs_workspace_name_version"),
        Index("ix_specs_workspace_id", "workspace_id"),
        Index("ix_specs_created_by_id", "created_by_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
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
    created_by_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="specs")
    created_by: Mapped["User"] = relationship("User")
    drafts: Mapped[list["SpecDraft"]] = relationship("SpecDraft", back_populates="source_spec")


class SpecDraft(TimestampMixin, Base):
    """User-defined specification drafts in the spec builder.

    Stores the working state of a spec being built, allowing users to
    save progress and resume later. Users can have multiple drafts within
    a workspace, each with a unique name.
    """

    __tablename__ = "spec_drafts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "name", name="uq_spec_drafts_workspace_user_name"
        ),
        Index("ix_spec_drafts_workspace_id", "workspace_id"),
        Index("ix_spec_drafts_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    workspace_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
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
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="spec_drafts")
    user: Mapped["User"] = relationship("User")
    source_spec: Mapped["Spec | None"] = relationship("Spec", back_populates="drafts")


__all__ = [
    "Base",
    "ChatMessage",
    "Note",
    "Project",
    "SoftDeleteMixin",
    "Spec",
    "SpecDraft",
    "SpecStatus",
    "Team",
    "TeamMembership",
    "TeamRole",
    "Tenant",
    "TimestampMixin",
    "User",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceRole",
    "WorkspaceTeam",
]
