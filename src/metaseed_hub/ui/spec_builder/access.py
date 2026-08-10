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
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metaseed_hub.database import get_session
from metaseed_hub.models import (
    Dataset,
    Spec,
    SpecDraft,
    SpecDraftMember,
    SpecDraftRole,
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


class SpecInUseError(Exception):
    """A published spec cannot be withdrawn while datasets are built on it.

    Withdrawing soft-deletes the spec, which removes it from every query at
    once — including the profile lookup that a dataset performs on every page
    load. Datasets bind to a specification by name and version, and published
    specs are visible hub-wide, so the datasets that break are usually other
    people's: acdc_ks 2.0 was withdrawn on 260728 and two datasets in another
    account raised SpecLoadError on every page from then on.
    """

    def __init__(self, spec_name: str, version: str, datasets: list[str]) -> None:
        self.datasets = datasets
        listed = ", ".join(datasets[:5])
        more = f" and {len(datasets) - 5} more" if len(datasets) > 5 else ""
        super().__init__(
            f"{len(datasets)} dataset(s) are built on {spec_name} {version} "
            f"({listed}{more}). Withdrawing it would break them on every page "
            "load. Move them to another specification first, or leave this one "
            "published."
        )


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
    """Whether ``user_id`` may edit — and so also withdraw — a published spec.

    Its author, and nobody else. There was a second branch granting this to
    admins and owners of a team in the same tenant, but teams were removed from
    the hub: no interface created one, the table held no rows, and the branch
    could never be true. Sharing a published specification with another person
    is not possible today (``spec_members`` has no interface writing to it);
    when it returns, it belongs here.
    """
    result = await session.execute(
        select(Spec).where(Spec.id == spec_id, Spec.deleted_at.is_(None))
    )
    spec = result.scalar_one_or_none()
    if not spec:
        return False

    if spec.created_by_id == user_id:
        return True

    return False


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
      grant exists so account colleagues can read a draft without being
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

    # A name is a label in a list; another draft holding it must not cost the
    # user this save. See free_draft_name.
    draft.name = await free_draft_name(
        session,
        user_id=draft.user_id,
        tenant_id=draft.tenant_id,
        wanted=state.spec.name or draft.name,
        exclude_draft_id=draft.id,
    )
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


async def delete_draft(session: AsyncSession, draft: SpecDraft) -> list[str]:
    """Remove a draft, unless datasets are built on it.

    The draft holds the specification those datasets validate against, so
    deleting it would leave them without one; they are reported instead and
    nothing is removed. The cached state goes with the row, or a later read in
    this process would serve a draft that no longer exists.

    The caller is responsible for authorization: this deletes whatever draft it
    is given.

    Args:
        session: The session to delete in.
        draft: The draft to remove.

    Returns:
        The names of the datasets still built on the draft. A non-empty list
        means nothing was deleted.
    """
    result = await session.execute(
        select(Dataset.name).where(Dataset.spec_draft_id == draft.id, Dataset.deleted_at.is_(None))
    )
    dependents = list(result.scalars().all())
    if dependents:
        return dependents

    draft_id = draft.id
    await session.delete(draft)
    await session.commit()
    state_cache.pop(draft_id, None)
    return []


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


async def datasets_using_spec(session: AsyncSession, spec: Spec) -> list[str]:
    """Names of live datasets built on ``spec``, in any account.

    Datasets bind by profile name and version rather than by foreign key —
    ``spec_id`` is null on datasets created from a published spec — so matching
    on the id alone finds nothing. Published specs are hub-wide, so the search
    deliberately crosses tenants: the datasets at risk are usually not the
    publisher's own.
    """
    from metaseed_hub.models import Dataset

    result = await session.execute(
        select(Dataset.name).where(
            Dataset.deleted_at.is_(None),
            or_(
                Dataset.spec_id == spec.id,
                and_(Dataset.profile == spec.name, Dataset.version == spec.version),
            ),
        )
    )
    return list(result.scalars().all())


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
        SpecInUseError: If datasets are built on this specification. The check
            lives here rather than in the route so every caller is covered —
            the API and the MCP tools withdraw through this function too.
    """
    in_use = await datasets_using_spec(session, spec)
    if in_use:
        raise SpecInUseError(spec.name, spec.version, in_use)

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


async def account_owner(session: AsyncSession, tenant_id: str) -> User | None:
    """The person whose account holds an item.

    An account belongs to exactly one person, so this is who to approach about
    a specification that lives there. It is not always the author: publishing a
    draft someone shared with you puts the specification in *their* account
    while recording you as its author, and without both names on the page that
    is invisible — a spec published this way appears to vanish for its author
    and to appear from nowhere for the owner.

    Args:
        session: Database session.
        tenant_id: The account.

    Returns:
        The owning user, or None if the account has no live user.
    """
    result = await session.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.deleted_at.is_(None))
        .order_by(User.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def free_draft_name(
    session: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    wanted: str,
    exclude_draft_id: str | None = None,
) -> str:
    """Return a draft name ``user_id`` can hold, suffixing ``wanted`` if taken.

    Draft names are unique per user (``uq_spec_drafts_tenant_user_name``), and a
    draft's row name is rewritten from its spec on every save. Two drafts whose
    specs share a name therefore collided, and the IntegrityError took down
    saving, deleting a field, and importing alike -- leaving the draft
    unsavable. A name is a label in a list; losing someone's edit to a clash
    between two labels is the wrong trade.

    The suffix is the draft's own id, not a counter, so it is the same on every
    save. A counter would have to re-derive itself each time and would walk
    ``-2``, ``-3``, ``-4`` as the draft was saved again.

    Args:
        session: Database session.
        user_id: Database ``User.id`` of the owner.
        tenant_id: Tenant the draft belongs to.
        wanted: The preferred name.
        exclude_draft_id: The draft being saved, whose own name is not a clash.

    Returns:
        ``wanted`` when it is free, otherwise ``wanted-<short id>``.
    """
    result = await session.execute(
        select(SpecDraft.id, SpecDraft.name).where(
            SpecDraft.user_id == user_id,
            SpecDraft.tenant_id == tenant_id,
        )
    )
    taken = {name for draft_id, name in result.all() if draft_id != exclude_draft_id}
    if wanted not in taken:
        return wanted
    if exclude_draft_id:
        return f"{wanted}-{exclude_draft_id.split('-')[0]}"
    # No draft to key the suffix to yet (a create): fall back to a counter,
    # which is stable because the row does not exist to be renamed again.
    suffix = 2
    while f"{wanted}-{suffix}" in taken:
        suffix += 1
    return f"{wanted}-{suffix}"
