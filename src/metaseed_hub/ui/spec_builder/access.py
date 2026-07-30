"""Access control helpers for spec builder.

Provides authentication, authorization, and tenant access functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metaseed_hub.database import get_session
from metaseed_hub.models import (
    Spec,
    SpecDraft,
    SpecDraftMember,
    SpecDraftRole,
    Team,
    TeamMembership,
    TeamRole,
    User,
)

from .cache import state_cache
from .state import SpecBuilderState

logger = logging.getLogger(__name__)

# Distinguishes "caller did not say" from an explicit None, which means "I hold
# a copy of unknown age" and must be treated as a conflict.
_UNSET_REVISION: Any = object()


class LoginRequiredRedirectError(Exception):
    """Raised when user needs to login and should be redirected."""

    pass


class DraftConflictError(Exception):
    """Raised when a save would overwrite an edit made since the state loaded.

    Saving rewrites the draft's whole ``spec_data``, so a writer holding an
    older copy would silently destroy whatever landed in between. Callers turn
    this into a message telling the user to reload, rather than reporting a
    success that discards someone's work.
    """


def handle_draft_conflict(request: Request, exc: Exception) -> Response:
    """Report a refused save to the user instead of a false success.

    Rendered as the same notification partial the editing routes swap in, so the
    message lands where the user is looking whichever control they used.
    """
    logger.warning("Refused a spec-draft save that would overwrite a newer edit: %s", exc)
    return HTMLResponse(
        "<div class='notification notification-error'>"
        "This spec changed somewhere else since you opened it, so saving would "
        "have discarded that change. Reload the page to pick up the current "
        "version, then reapply your edit."
        "</div>",
        status_code=409,
    )


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
    from metaseed_hub.ui.dependencies import ensure_tenant_and_user, get_current_user_from_cookie

    token_user = await get_current_user_from_cookie(request)

    if not token_user:
        if redirect_on_unauthorized:
            raise LoginRequiredRedirectError()
        raise HTTPException(status_code=401, detail="Login required")

    # Provision the tenant and user through the canonical helper so onboarding
    # behaves identically here and on the other entry points.
    tenant, user = await ensure_tenant_and_user(session, token_user)
    return user.id, tenant.id


async def can_edit_spec(
    session: AsyncSession,
    user_id: str,
    spec_id: str,
) -> bool:
    """Check if user can edit spec (tenant member with ADMIN/OWNER role or author)."""
    result = await session.execute(
        select(Spec).where(Spec.id == spec_id, Spec.deleted_at.is_(None))
    )
    spec = result.scalar_one_or_none()
    if not spec:
        return False

    if spec.created_by_id == user_id:
        return True

    # Check if user is admin/owner in a team within the same tenant.
    # The WHERE clause fully constrains the result to this caller's memberships;
    # joining User would multiply rows (one per tenant user) and break scalar_one_or_none.
    result = await session.execute(
        select(TeamMembership)
        .join(Team, TeamMembership.team_id == Team.id)
        .where(
            Team.tenant_id == spec.tenant_id,
            TeamMembership.user_id == user_id,
            TeamMembership.role.in_([TeamRole.ADMIN, TeamRole.OWNER]),
        )
    )
    return result.scalars().first() is not None


# Roles allowed to modify a draft's specification. VIEWER is read-only.
_EDIT_ROLES = frozenset({SpecDraftRole.EDITOR, SpecDraftRole.OWNER})

# Request methods that never modify a draft; everything else must be gated on
# an edit-capable role.
_READ_METHODS = frozenset({"GET", "HEAD"})


async def get_draft_role(
    session: AsyncSession, draft: SpecDraft, user_id: str
) -> SpecDraftRole | None:
    """Resolve the caller's effective role on a draft.

    Access is granted through three paths, checked in order of precedence:

    - The draft owner (``draft.user_id``, a User.id FK) holds ``OWNER``.
    - An explicit ``SpecDraftMember`` row holds its recorded role.
    - Any other user in the draft's tenant holds ``VIEWER``. This tenant-wide
      grant exists so workspace colleagues can read a draft without being
      shared on it; it never confers edit rights.

    Args:
        session: Database session
        draft: The draft to check access for
        user_id: Database User.id (not keycloak_id)

    Returns:
        The effective role, or None if the user may not access the draft.
    """
    if draft.user_id == user_id:
        return SpecDraftRole.OWNER

    member_result = await session.execute(
        select(SpecDraftMember).where(
            SpecDraftMember.spec_draft_id == draft.id,
            SpecDraftMember.user_id == user_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if member is not None:
        role: SpecDraftRole = member.role
        return role

    result = await session.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == draft.tenant_id,
        )
    )
    if result.scalar_one_or_none() is not None:
        return SpecDraftRole.VIEWER
    return None


async def can_edit_draft(session: AsyncSession, draft: SpecDraft, user_id: str) -> bool:
    """Whether the caller may modify the draft's specification content.

    Args:
        session: Database session.
        draft: The draft to check.
        user_id: Database User.id (not keycloak_id).

    Returns:
        True if the caller holds an EDITOR or OWNER role on the draft.
    """
    return await get_draft_role(session, draft, user_id) in _EDIT_ROLES


async def require_edit_role(session: AsyncSession, draft: SpecDraft, user_id: str) -> None:
    """Require the caller to hold an edit-capable role on a draft.

    Gates every route that modifies the draft's specification. Members shared
    with the VIEWER role, and tenant colleagues who were never shared on the
    draft at all, may read it but not change it.

    Args:
        session: Database session.
        draft: The draft being modified.
        user_id: Database User.id (not keycloak_id) of the caller.

    Raises:
        HTTPException: 403 if the caller does not hold EDITOR or OWNER.
    """
    if not await can_edit_draft(session, draft, user_id):
        raise HTTPException(
            status_code=403, detail="Viewer access only: editing requires the editor or owner role"
        )


async def require_owner_role(session: AsyncSession, draft: SpecDraft, user_id: str) -> None:
    """Require the caller to hold the OWNER role on a draft.

    Gates the destructive draft-level operations (publish, reset), which are
    granted to the draft owner and to members explicitly given the OWNER role,
    but not to editors or viewers.

    Args:
        session: Database session.
        draft: The draft being operated on.
        user_id: Database User.id (not keycloak_id) of the caller.

    Raises:
        HTTPException: 403 if the caller's effective role is not OWNER.
    """
    if await get_draft_role(session, draft, user_id) is not SpecDraftRole.OWNER:
        raise HTTPException(status_code=403, detail="Only a draft owner may do this")


async def require_draft_owner(
    session: AsyncSession,
    draft_id: str,
    user_id: str,
) -> SpecDraft:
    """Load a draft, requiring the caller to be its owner.

    Used to gate membership management, which only the draft owner may perform.

    Args:
        session: Database session.
        draft_id: Draft identifier.
        user_id: Database User.id (not keycloak_id) of the caller.

    Returns:
        The draft owned by the caller.

    Raises:
        HTTPException: 404 if the draft does not exist, 403 if the caller does
            not own it.
    """
    result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return draft


async def require_draft_access(
    session: AsyncSession,
    draft_id: str,
    user_id: str,
) -> SpecDraft:
    """Load a draft, requiring the caller to hold any role on it.

    Used to gate reading and commenting on a draft. Access is granted to the
    draft owner, to explicit members regardless of role, and to any user in
    the draft's tenant (see ``get_draft_role``).

    Args:
        session: Database session.
        draft_id: Draft identifier.
        user_id: Database User.id (not keycloak_id) of the caller.

    Returns:
        The draft the caller may access.

    Raises:
        HTTPException: 404 if the draft does not exist, 403 if the caller may
            not access it.
    """
    result = await session.execute(select(SpecDraft).where(SpecDraft.id == draft_id))
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if await get_draft_role(session, draft, user_id) is None:
        raise HTTPException(status_code=403, detail="Access denied")
    return draft


async def load_state_for_draft(
    session: AsyncSession,
    draft_id: str,
    user_id: str,
) -> tuple[SpecBuilderState, SpecDraft]:
    """Load state from database for a specific draft.

    Args:
        session: Database session
        draft_id: Draft ID to load
        user_id: Database User.id (not keycloak_id) for access control

    Returns:
        Tuple of (SpecBuilderState, SpecDraft)

    Raises:
        HTTPException 404 if draft not found, 403 if not accessible
    """
    result = await session.execute(
        select(SpecDraft).options(selectinload(SpecDraft.tenant)).where(SpecDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if await get_draft_role(session, draft, user_id) is None:
        raise HTTPException(status_code=403, detail="Access denied to this draft")

    # The cache is only trusted while the stored row has not moved on. Another
    # worker (production runs two) has its own cache and writes straight to the
    # row, so serving a cached copy unchecked meant the next save rewrote the
    # whole spec_data from a stale copy and silently dropped that worker's edit.
    cached_state = state_cache.get(draft_id)
    if cached_state is not None and state_cache.revision(draft_id) == draft.updated_at:
        return cached_state, draft

    if draft.spec_data:
        state = SpecBuilderState.from_dict(draft.spec_data)
    else:
        state = SpecBuilderState()

    state_cache.set(draft_id, state, revision=draft.updated_at)
    return state, draft


async def save_state_to_draft(
    session: AsyncSession,
    state: SpecBuilderState,
    draft: SpecDraft,
    expected_revision: datetime | None = _UNSET_REVISION,
) -> None:
    """Save state to an existing draft.

    Args:
        session: Database session.
        state: The state to persist; its spec replaces the draft's whole
            ``spec_data``.
        draft: The draft row to write.
        expected_revision: The ``updated_at`` the caller's state was read at.
            Defaults to the revision recorded when the state was cached, which
            is what the request handlers want. Pass ``None`` to say "I do not
            know", which is treated as a conflict whenever the row has moved.

    Raises:
        DraftConflictError: If the row changed since the state was read. Saving
            anyway would rewrite ``spec_data`` from an older copy and destroy
            the intervening edit while reporting success.
        ValueError: If the state holds no spec; there is nothing meaningful to
            persist and writing it would empty the draft.
    """
    from sqlalchemy.orm.attributes import flag_modified

    if state.spec is None:
        raise ValueError("Cannot save a state with no spec")

    if expected_revision is _UNSET_REVISION:
        expected_revision = state_cache.revision(draft.id)
    stored_revision = await _stored_revision(session, draft.id)
    if stored_revision is not None and expected_revision != stored_revision:
        # Drop the stale copy so the next read rebuilds from the row rather than
        # handing the same doomed state back.
        state_cache.pop(draft.id, None)
        raise DraftConflictError(draft.id)

    spec_data = state.to_dict()

    draft.name = state.spec.name or draft.name
    draft.version = state.spec.version
    draft.spec_data = spec_data
    flag_modified(draft, "spec_data")
    if state.template_source:
        draft.template_source = f"{state.template_source[0]}:{state.template_source[1]}"

    await session.commit()
    await session.refresh(draft)
    state_cache.set(draft.id, state, revision=draft.updated_at)


async def _stored_revision(session: AsyncSession, draft_id: str) -> datetime | None:
    """Read the draft row's current ``updated_at``, or None if it is gone."""
    result = await session.execute(select(SpecDraft.updated_at).where(SpecDraft.id == draft_id))
    return result.scalar_one_or_none()


async def create_new_draft(
    session: AsyncSession,
    user_id: str,
    tenant_id: str,
    name: str,
    spec: ProfileSpec,
    template_source: tuple[str, str] | None = None,
    source_spec_id: str | None = None,
) -> SpecDraft:
    """Create a new draft in a tenant."""
    state = SpecBuilderState()
    state.spec = spec
    state.template_source = template_source

    draft = SpecDraft(
        user_id=user_id,
        tenant_id=tenant_id,
        source_spec_id=source_spec_id,
        name=name,
        version=spec.version,
        spec_data=state.to_dict(),
        template_source=(f"{template_source[0]}:{template_source[1]}" if template_source else None),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)

    # Tag the entry with the row revision, as every other cache write does;
    # an untagged entry never matches the revision check and is always stale.
    state_cache.set(draft.id, state, revision=draft.updated_at)
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
    """FastAPI dependency that loads and validates draft context.

    For mutating request methods (anything other than GET/HEAD) the caller
    must additionally hold an EDITOR or OWNER role on the draft, so a member
    shared as VIEWER can read every panel but cannot change the spec.

    Raises:
        HTTPException: 404 if the draft does not exist, 403 if the caller may
            not access it or holds only read access on a mutating request,
            400 if the draft holds no spec.
    """
    user_id, tenant_id = await get_user_context(request, session)
    builder, draft = await load_state_for_draft(session, draft_id, user_id)

    if request.method not in _READ_METHODS:
        await require_edit_role(session, draft, user_id)

    if builder.spec is None:
        raise HTTPException(status_code=400, detail="No spec in progress")

    return DraftContext(
        builder=builder,
        draft=draft,
        user_id=user_id,
        tenant_id=tenant_id,
    )


async def unpublish_spec(
    session: AsyncSession,
    spec: Spec,
    user_id: str,
) -> SpecDraft:
    """Withdraw a published spec and hand it back as a private draft.

    Publishing is one-way: it replaces an editable draft with an immutable
    published specification and deletes the draft, leaving no way back short of
    editing the database by hand. This is the way back.

    The spec is soft-deleted rather than erased, so it leaves every tenant-scoped
    query at once — the Specs page, the profile choices for a new dataset, the
    Explorer, and anyone it was shared with — while an administrator can still
    account for what existed. The specification itself is not lost: it returns as
    a draft owned by the caller, who can fix whatever was wrong and publish
    again.

    Args:
        session: Database session.
        spec: The published spec to withdraw. Caller must already have checked
            that this user may edit it.
        user_id: Who is unpublishing, and who owns the resulting draft.

    Returns:
        The new private draft carrying the specification.

    Raises:
        ValueError: If the spec holds no usable specification, in which case
            there is nothing to hand back and the withdrawal is not performed.
    """
    builder = SpecBuilderState.from_dict(spec.spec_data) if spec.spec_data else SpecBuilderState()
    if builder.spec is None:
        raise ValueError(f"Spec {spec.id} holds no specification to restore")

    # Marked before create_new_draft commits, so the withdrawal and the draft
    # land in one transaction: a failure must not leave the spec visible with a
    # duplicate draft beside it, nor withdraw it with the work gone.
    spec.soft_delete()

    return await create_new_draft(
        session,
        user_id=user_id,
        tenant_id=spec.tenant_id,
        name=spec.name,
        spec=builder.spec,
        source_spec_id=spec.id,
    )


async def workspace_owner(session: AsyncSession, tenant_id: str) -> User | None:
    """The person whose workspace holds an item.

    A workspace belongs to exactly one person, so this is who to approach about
    a specification that lives there. It is not always the author: publishing a
    draft someone shared with you puts the specification in *their* workspace
    while recording you as its author, and without both names on the page that
    is invisible — a spec published this way appears to vanish for its author
    and to appear from nowhere for the owner.

    Args:
        session: Database session.
        tenant_id: The workspace.

    Returns:
        The owning user, or None if the workspace has no live user.
    """
    result = await session.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.deleted_at.is_(None))
        .order_by(User.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()
