"""What a profile picker value binds a new dataset to.

The New and Import File tabs offer the same choices: built-in standards, the
caller's drafts as ``draft:<name>`` and published specifications as
``spec:<id>``. Both routes resolve them here, so a dataset imported against a
draft is bound to it exactly as one created from the New tab is; the import
route used to take the value verbatim and store the literal profile
``draft:foo`` with no binding.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.audience import visible_specs
from metaseed_hub.models import Spec, SpecDraft, SpecDraftMember, SpecStatus


class ProfileChoiceNotFoundError(Exception):
    """A ``draft:`` or ``spec:`` picker value names nothing the caller may use."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def draft_for_choice(
    session: AsyncSession,
    profile: str,
    version: str,
    tenant_id: str,
    user_id: str,
) -> SpecDraft | None:
    """The draft a picker choice names, or None when it reaches none.

    ``profile`` is ``draft:`` followed by the specification's name, with
    ``version`` choosing between that name's drafts; a key holding an id is
    what earlier pages sent, and still resolves.

    Scoped to what the caller may reach, matching the picker: owned by their
    tenant or shared with them. An unscoped lookup would let a person bind
    their dataset to another account's draft.
    """
    wanted = profile.removeprefix("draft:")
    try:
        UUID(wanted)
    except ValueError:
        identifies = and_(SpecDraft.name == wanted, SpecDraft.version == version)
    else:
        identifies = SpecDraft.id == wanted
    found = await session.execute(
        select(SpecDraft)
        .outerjoin(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
        .where(
            identifies,
            or_(
                SpecDraft.tenant_id == tenant_id,
                SpecDraftMember.user_id == user_id,
            ),
        )
    )
    return found.scalars().first()


async def resolve_profile_choice(
    session: AsyncSession, profile: str, version: str, tenant_id: str, user_id: str
) -> tuple[str, str, str | None, str | None]:
    """Resolve a picker value to a profile, a version and a binding.

    Args:
        session: Database session.
        profile: The picker value.
        version: The chosen version; for a draft, which draft of that name.
        tenant_id: The caller's account.
        user_id: The caller, whose drafts and visible publications count.

    Returns:
        ``(profile, version, spec_draft_id, spec_id)``: the lowercased profile
        name the facade expects, the resolved version, and the binding.

    Raises:
        ProfileChoiceNotFoundError: With the error code to send the caller
            back with, when the draft or publication cannot be found.
    """
    if profile.startswith("draft:"):
        draft = await draft_for_choice(session, profile, version, tenant_id, user_id)
        if draft is None:
            raise ProfileChoiceNotFoundError("draft_not_found")
        # Lowercase to match ProfileFacade behavior
        return draft.name.lower(), draft.version, draft.id, None
    if profile.startswith("spec:"):
        # A published specification, chosen from any account: publishing is
        # what makes one available to other people, so this is not scoped to
        # the caller. Only PUBLISHED and not withdrawn, so a draft stays
        # unreachable by id, and only what the caller may see: hidden on the
        # picker is not hidden if the id still works.
        spec_id = profile.replace("spec:", "")
        spec_result = await session.execute(
            select(Spec).where(
                Spec.id == spec_id,
                Spec.status == SpecStatus.PUBLISHED,
                Spec.deleted_at.is_(None),
                await visible_specs(session, user_id),
            )
        )
        published = spec_result.scalar_one_or_none()
        if published is None:
            raise ProfileChoiceNotFoundError("spec_not_found")
        return published.name.lower(), published.version, None, published.id
    return profile, version, None, None
