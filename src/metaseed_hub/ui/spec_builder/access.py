"""Access control helpers for spec builder.

Provides authentication, authorization, and workspace access functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metaseed_hub.database import get_session
from metaseed_hub.models import (
    Spec,
    SpecDraft,
    SpecDraftMember,
    Team,
    TeamMembership,
    TeamRole,
    Tenant,
    User,
    Workspace,
    WorkspaceTeam,
)

from .cache import state_cache
from .state import SpecBuilderState

logger = logging.getLogger(__name__)


class LoginRequiredRedirectError(Exception):
    """Raised when user needs to login and should be redirected."""

    pass


async def get_user_context(
    request: Request,
    session: AsyncSession,
    *,
    redirect_on_unauthorized: bool = False,
) -> tuple[str, str]:
    """Get user_id and tenant_id from request context.

    Args:
        request: The FastAPI request
        session: Database session
        redirect_on_unauthorized: If True, raise LoginRequiredRedirectError

    Returns:
        Tuple of (user_id, tenant_id)

    Raises:
        HTTPException 401 if user is not authenticated (API routes)
        LoginRequiredRedirectError if redirect_on_unauthorized=True (page routes)
    """
    from metaseed_hub.ui.dependencies import get_current_user_from_cookie

    token_user = await get_current_user_from_cookie(request)

    if not token_user:
        if redirect_on_unauthorized:
            raise LoginRequiredRedirectError()
        raise HTTPException(status_code=401, detail="Login required")

    # Get or create tenant for this user
    slug = token_user.keycloak_id[:8]
    tenant_result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    tenant: Tenant | None = tenant_result.scalar_one_or_none()
    if not tenant:
        tenant = Tenant(name=token_user.name or token_user.email, slug=slug)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
    assert tenant is not None

    # Look up or create the User record
    user_result = await session.execute(
        select(User).where(
            User.keycloak_id == token_user.keycloak_id,
            User.tenant_id == tenant.id,
        )
    )
    user: User | None = user_result.scalar_one_or_none()
    if not user:
        user = User(
            keycloak_id=token_user.keycloak_id,
            tenant_id=tenant.id,
            email=token_user.email,
            display_name=token_user.name or token_user.email,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    assert user is not None

    return user.id, tenant.id


async def get_user_workspaces(
    session: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> list[Workspace]:
    """Get workspaces accessible to user through team membership.

    A user can access a workspace if:
    - They are a member of a team that has access to the workspace
    - OR the workspace belongs to their tenant (temporary fallback for migration)

    Args:
        session: Database session
        user_id: User ID
        tenant_id: Tenant ID

    Returns:
        List of accessible workspaces
    """
    result = await session.execute(
        select(Workspace)
        .join(WorkspaceTeam, Workspace.id == WorkspaceTeam.workspace_id)
        .join(Team, WorkspaceTeam.team_id == Team.id)
        .join(TeamMembership, Team.id == TeamMembership.team_id)
        .where(
            TeamMembership.user_id == user_id,
            Workspace.deleted_at.is_(None),
        )
        .distinct()
    )
    team_workspaces = list(result.scalars().all())

    if team_workspaces:
        return team_workspaces

    # Fallback: return all workspaces in the tenant
    logger.warning(
        "User %s has no team memberships, falling back to tenant-wide workspace access.",
        user_id,
    )
    result = await session.execute(
        select(Workspace).where(
            Workspace.tenant_id == tenant_id,
            Workspace.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def can_access_workspace(
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
) -> bool:
    """Check if user has access to workspace via team membership."""
    result = await session.execute(
        select(WorkspaceTeam)
        .join(Team, WorkspaceTeam.team_id == Team.id)
        .join(TeamMembership, Team.id == TeamMembership.team_id)
        .where(
            WorkspaceTeam.workspace_id == workspace_id,
            TeamMembership.user_id == user_id,
        )
    )
    if result.scalar_one_or_none():
        return True

    # Fallback: check if workspace is in user's tenant
    result = await session.execute(
        select(Workspace)
        .join(User, Workspace.tenant_id == User.tenant_id)
        .where(
            Workspace.id == workspace_id,
            User.id == user_id,
            Workspace.deleted_at.is_(None),
        )
    )
    has_fallback_access = result.scalar_one_or_none() is not None
    if has_fallback_access:
        logger.warning(
            "User %s accessing workspace %s via tenant fallback.",
            user_id,
            workspace_id,
        )
    return has_fallback_access


async def can_edit_spec(
    session: AsyncSession,
    user_id: str,
    spec_id: str,
) -> bool:
    """Check if user can edit spec (workspace member with ADMIN/OWNER role or author)."""
    result = await session.execute(
        select(Spec).where(Spec.id == spec_id, Spec.deleted_at.is_(None))
    )
    spec = result.scalar_one_or_none()
    if not spec:
        return False

    if spec.created_by_id == user_id:
        return True

    result = await session.execute(
        select(TeamMembership)
        .join(Team, TeamMembership.team_id == Team.id)
        .join(WorkspaceTeam, Team.id == WorkspaceTeam.team_id)
        .where(
            WorkspaceTeam.workspace_id == spec.workspace_id,
            TeamMembership.user_id == user_id,
            TeamMembership.role.in_([TeamRole.ADMIN, TeamRole.OWNER]),
        )
    )
    return result.scalar_one_or_none() is not None


async def get_or_create_default_workspace(
    session: AsyncSession,
    tenant_id: str,
) -> Workspace:
    """Get or create a default workspace for a tenant."""
    result = await session.execute(
        select(Workspace)
        .where(
            Workspace.tenant_id == tenant_id,
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at)
    )
    workspace = result.scalars().first()

    if workspace:
        return workspace

    workspace = Workspace(
        tenant_id=tenant_id,
        name="Default",
        description="Default workspace",
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


async def _user_can_access_draft(session: AsyncSession, draft: SpecDraft, keycloak_id: str) -> bool:
    """Check if user can access a draft (owner or member)."""
    # Owner can always access
    if draft.user_id == keycloak_id:
        return True

    # Check if user is a member
    user_result = await session.execute(select(User).where(User.keycloak_id == keycloak_id))
    db_user = user_result.scalar_one_or_none()
    if not db_user:
        return False

    member_result = await session.execute(
        select(SpecDraftMember).where(
            SpecDraftMember.spec_draft_id == draft.id,
            SpecDraftMember.user_id == db_user.id,
        )
    )
    return member_result.scalar_one_or_none() is not None


async def load_state_for_draft(
    session: AsyncSession,
    draft_id: str,
    user_id: str,
) -> tuple[SpecBuilderState, SpecDraft]:
    """Load state from database for a specific draft.

    Args:
        session: Database session
        draft_id: Draft ID to load
        user_id: User ID (keycloak_id for access control)

    Returns:
        Tuple of (SpecBuilderState, SpecDraft)

    Raises:
        HTTPException 404 if draft not found or not accessible
    """
    cached_state = state_cache.get(draft_id)
    if cached_state is not None:
        result = await session.execute(
            select(SpecDraft)
            .options(selectinload(SpecDraft.workspace))
            .where(SpecDraft.id == draft_id)
        )
        draft = result.scalar_one_or_none()
        if draft and await _user_can_access_draft(session, draft, user_id):
            return cached_state, draft

    result = await session.execute(
        select(SpecDraft).options(selectinload(SpecDraft.workspace)).where(SpecDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if not await _user_can_access_draft(session, draft, user_id):
        raise HTTPException(status_code=403, detail="Access denied to this draft")

    if draft.spec_data:
        state = SpecBuilderState.from_dict(draft.spec_data)
    else:
        state = SpecBuilderState()

    state_cache.set(draft_id, state)
    return state, draft


async def save_state_to_draft(
    session: AsyncSession,
    state: SpecBuilderState,
    draft: SpecDraft,
) -> None:
    """Save state to an existing draft."""
    from sqlalchemy.orm.attributes import flag_modified

    if state.spec is None:
        await session.delete(draft)
        await session.commit()
        state_cache.pop(draft.id, None)
        return

    spec_data = state.to_dict()

    draft.name = state.spec.name or draft.name
    draft.version = state.spec.version
    draft.spec_data = spec_data
    flag_modified(draft, "spec_data")
    if state.template_source:
        draft.template_source = f"{state.template_source[0]}:{state.template_source[1]}"

    await session.commit()
    state_cache.set(draft.id, state)


async def create_new_draft(
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
    name: str,
    spec: ProfileSpec,
    template_source: tuple[str, str] | None = None,
    source_spec_id: str | None = None,
) -> SpecDraft:
    """Create a new draft in a workspace."""
    state = SpecBuilderState()
    state.spec = spec
    state.template_source = template_source

    draft = SpecDraft(
        user_id=user_id,
        workspace_id=workspace_id,
        source_spec_id=source_spec_id,
        name=name,
        version=spec.version,
        spec_data=state.to_dict(),
        template_source=(f"{template_source[0]}:{template_source[1]}" if template_source else None),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)

    state_cache.set(draft.id, state)
    return draft


@dataclass
class DraftContext:
    """Context for draft-specific route handlers.

    Encapsulates user context, draft, and builder state needed by endpoints.
    """

    builder: SpecBuilderState
    draft: SpecDraft
    user_id: str
    tenant_id: str

    @property
    def spec(self) -> ProfileSpec:
        """Get the spec, guaranteed to be non-None."""
        assert self.builder.spec is not None
        return self.builder.spec

    async def save(self, session: AsyncSession) -> None:
        """Save the current state to the draft."""
        await save_state_to_draft(session, self.builder, self.draft)


async def get_draft_context(
    request: Request,
    draft_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DraftContext:
    """FastAPI dependency that loads and validates draft context."""
    user_id, tenant_id = await get_user_context(request, session)
    builder, draft = await load_state_for_draft(session, draft_id, user_id)

    if builder.spec is None:
        raise HTTPException(status_code=400, detail="No spec in progress")

    return DraftContext(
        builder=builder,
        draft=draft,
        user_id=user_id,
        tenant_id=tenant_id,
    )
