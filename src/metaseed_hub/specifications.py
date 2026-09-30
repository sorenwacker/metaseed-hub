"""What a profile name and version refer to on this hub.

Three things can answer to a name: a profile installed with metaseed, a
specification published on the hub, and a draft of the caller's own. The web
New Dataset picker binds a draft through ``spec_draft_id`` and the MCP
``create_dataset`` tool resolved a publication by name, but the REST create --
what ``metaseed hub push-dataset`` calls -- looked among the installed profiles
only, so a dataset built on a profile pushed to the hub was refused (#177).
One resolution serves every interface now.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from metaseed_hub.audience import visible_specs
from metaseed_hub.models import Spec, SpecDraft, SpecStatus


class UnknownProfileError(LookupError):
    """The hub holds nothing that answers to the profile name and version."""

    def __init__(self, profile: str, version: str) -> None:
        super().__init__(
            f"No profile named {profile!r} with version {version!r}: not installed on "
            "this hub, not published here, and not one of your drafts."
        )


def installed_versions(profile: str) -> list[str]:
    """The versions of ``profile`` installed with metaseed, empty if none."""
    from metaseed.specs.loader import SpecLoader

    loader = SpecLoader()
    if profile.lower() not in loader.list_profiles():
        return []
    return list(loader.list_versions(profile.lower()))


async def published_spec(
    session: AsyncSession,
    profile: str,
    version: str,
    prefer_tenant: str | None = None,
    *,
    for_user_id: str | None = None,
) -> Spec | None:
    """The published specification a profile name and version refer to, if any.

    Publishing shares a specification with whoever it was published to, so the
    lookup is not scoped to the caller's tenant but is scoped to what they may
    see: a specification published to a collaboration they are not in does not
    exist for them. Matched case-insensitively because datasets store the
    lowercased profile name while list_profiles reports the name as published.

    When two tenants published the same name and version, ``.first()`` on an
    unordered query handed the caller whichever row the database returned --
    possibly another tenant's specification. The caller's own tenant wins the
    collision; across other tenants the oldest publication wins, so the answer
    is at least deterministic.
    """
    ordering: list[ColumnElement[Any]] = [Spec.created_at.asc(), Spec.id.asc()]
    if prefer_tenant is not None:
        ordering.insert(0, case((Spec.tenant_id == prefer_tenant, 0), else_=1))

    result = await session.execute(
        select(Spec)
        .where(
            func.lower(Spec.name) == profile.lower(),
            Spec.version == version,
            Spec.status == SpecStatus.PUBLISHED,
            Spec.deleted_at.is_(None),
            await visible_specs(session, for_user_id),
        )
        .order_by(*ordering)
    )
    return result.scalars().first()


async def own_draft(
    session: AsyncSession, user_id: str, profile: str, version: str
) -> SpecDraft | None:
    """The caller's own draft of that name and version, if any.

    Drafts are private, so only the caller's are considered; the hub keeps one
    draft per name and version for a user.
    """
    result = await session.execute(
        select(SpecDraft).where(
            SpecDraft.user_id == user_id,
            func.lower(SpecDraft.name) == profile.lower(),
            SpecDraft.version == version,
        )
    )
    return result.scalars().first()


async def resolve_specification(
    session: AsyncSession,
    profile: str,
    version: str,
    *,
    tenant_id: str,
    user_id: str | None,
) -> tuple[str | None, str | None]:
    """What a dataset on ``profile``/``version`` is bound to: ``(spec_id, spec_draft_id)``.

    An installed profile first (both None: the loader resolves it by name),
    then a publication visible to the caller, then the caller's own draft. A
    release is what other people build on, so it wins over a draft of the same
    name; the draft is work in progress.

    Raises:
        UnknownProfileError: If the hub holds nothing under that name and version.
    """
    if version in installed_versions(profile):
        return None, None
    published = await published_spec(
        session, profile, version, prefer_tenant=tenant_id, for_user_id=user_id
    )
    if published is not None:
        return published.id, None
    if user_id is not None:
        draft = await own_draft(session, user_id, profile, version)
        if draft is not None:
            return None, draft.id
    raise UnknownProfileError(profile, version)
