"""Factory functions for creating test model instances."""

from typing import Any
from uuid import uuid4

from metaseed_hub.models import (
    ChatMessage,
    Note,
    Project,
    Spec,
    SpecDraft,
    SpecStatus,
    Team,
    TeamMembership,
    TeamRole,
    Tenant,
    User,
    Workspace,
    WorkspaceTeam,
)


def make_tenant(
    *,
    name: str = "Test Tenant",
    slug: str | None = None,
) -> Tenant:
    """Create a Tenant instance for testing.

    Args:
        name: Tenant display name.
        slug: URL-safe identifier. Auto-generated if not provided.

    Returns:
        Tenant model instance (not yet persisted).
    """
    return Tenant(
        name=name,
        slug=slug or f"tenant-{uuid4().hex[:8]}",
    )


def make_user(
    *,
    tenant: Tenant,
    email: str | None = None,
    keycloak_id: str | None = None,
    display_name: str = "Test User",
) -> User:
    """Create a User instance for testing.

    Args:
        tenant: Parent tenant for the user.
        email: User email address. Auto-generated if not provided.
        keycloak_id: Keycloak subject ID. Auto-generated if not provided.
        display_name: User display name.

    Returns:
        User model instance (not yet persisted).
    """
    suffix = uuid4().hex[:8]
    return User(
        tenant_id=tenant.id,
        email=email or f"user-{suffix}@example.com",
        keycloak_id=keycloak_id or f"kc-{suffix}",
        display_name=display_name,
    )


def make_team(
    *,
    tenant: Tenant,
    name: str | None = None,
) -> Team:
    """Create a Team instance for testing.

    Args:
        tenant: Parent tenant for the team.
        name: Team name. Auto-generated if not provided.

    Returns:
        Team model instance (not yet persisted).
    """
    return Team(
        tenant_id=tenant.id,
        name=name or f"Team {uuid4().hex[:8]}",
    )


def make_team_membership(
    *,
    user: User,
    team: Team,
    role: TeamRole = TeamRole.MEMBER,
) -> TeamMembership:
    """Create a TeamMembership instance for testing.

    Args:
        user: User to add to the team.
        team: Team to add the user to.
        role: Role within the team.

    Returns:
        TeamMembership model instance (not yet persisted).
    """
    return TeamMembership(
        user_id=user.id,
        team_id=team.id,
        role=role,
    )


def make_workspace(
    *,
    tenant: Tenant,
    name: str | None = None,
    description: str | None = None,
) -> Workspace:
    """Create a Workspace instance for testing.

    Args:
        tenant: Parent tenant for the workspace.
        name: Workspace name. Auto-generated if not provided.
        description: Optional workspace description.

    Returns:
        Workspace model instance (not yet persisted).
    """
    return Workspace(
        tenant_id=tenant.id,
        name=name or f"Workspace {uuid4().hex[:8]}",
        description=description,
    )


def make_project(
    *,
    workspace: Workspace,
    name: str | None = None,
    profile: str = "miappe",
    version: str = "1.1",
    data: dict | None = None,
) -> Project:
    """Create a Project instance for testing.

    Args:
        workspace: Parent workspace for the project.
        name: Project name. Auto-generated if not provided.
        profile: Metaseed profile type.
        version: Profile version.
        data: Optional JSONB data.

    Returns:
        Project model instance (not yet persisted).
    """
    return Project(
        workspace_id=workspace.id,
        name=name or f"Project {uuid4().hex[:8]}",
        profile=profile,
        version=version,
        data=data or {},
    )


def make_note(
    *,
    project: Project,
    user: User,
    entity_type: str = "Investigation",
    entity_id: str | None = None,
    content: str = "Test note content",
) -> Note:
    """Create a Note instance for testing.

    Args:
        project: Parent project for the note.
        user: User who created the note.
        entity_type: Type of entity the note is attached to.
        entity_id: Identifier of the entity. Auto-generated if not provided.
        content: Note content.

    Returns:
        Note model instance (not yet persisted).
    """
    return Note(
        project_id=project.id,
        user_id=user.id,
        entity_type=entity_type,
        entity_id=entity_id or f"entity-{uuid4().hex[:8]}",
        content=content,
    )


def make_chat_message(
    *,
    project: Project,
    user: User,
    content: str = "Test chat message",
) -> ChatMessage:
    """Create a ChatMessage instance for testing.

    Args:
        project: Parent project for the message.
        user: User who sent the message.
        content: Message content.

    Returns:
        ChatMessage model instance (not yet persisted).
    """
    return ChatMessage(
        project_id=project.id,
        user_id=user.id,
        content=content,
    )


def make_workspace_team(
    *,
    workspace: Workspace,
    team: Team,
) -> WorkspaceTeam:
    """Create a WorkspaceTeam association for testing.

    Args:
        workspace: Workspace to associate with team.
        team: Team to grant access to workspace.

    Returns:
        WorkspaceTeam model instance (not yet persisted).
    """
    return WorkspaceTeam(
        workspace_id=workspace.id,
        team_id=team.id,
    )


def make_spec(
    *,
    workspace: Workspace,
    created_by: User,
    name: str | None = None,
    version: str = "1.0.0",
    description: str | None = None,
    spec_data: dict[str, Any] | None = None,
    status: SpecStatus = SpecStatus.PUBLISHED,
) -> Spec:
    """Create a Spec instance for testing.

    Args:
        workspace: Parent workspace for the spec.
        created_by: User who created the spec.
        name: Spec name. Auto-generated if not provided.
        version: Spec version.
        description: Optional description.
        spec_data: Optional spec data.
        status: Spec status.

    Returns:
        Spec model instance (not yet persisted).
    """
    return Spec(
        workspace_id=workspace.id,
        created_by_id=created_by.id,
        name=name or f"Spec{uuid4().hex[:8]}",
        version=version,
        description=description,
        spec_data=spec_data or {},
        status=status,
    )


def make_spec_draft(
    *,
    workspace: Workspace,
    user: User,
    name: str | None = None,
    version: str = "0.1",
    spec_data: dict[str, Any] | None = None,
    source_spec: Spec | None = None,
    template_source: str | None = None,
) -> SpecDraft:
    """Create a SpecDraft instance for testing.

    Args:
        workspace: Parent workspace for the draft.
        user: User who owns the draft.
        name: Draft name. Auto-generated if not provided.
        version: Draft version.
        spec_data: Optional spec data.
        source_spec: Optional source spec (for editing published specs).
        template_source: Optional template source identifier.

    Returns:
        SpecDraft model instance (not yet persisted).
    """
    return SpecDraft(
        workspace_id=workspace.id,
        user_id=user.id,
        source_spec_id=source_spec.id if source_spec else None,
        name=name or f"Draft{uuid4().hex[:8]}",
        version=version,
        spec_data=spec_data or {},
        template_source=template_source,
    )
