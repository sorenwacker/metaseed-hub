"""Specifications over the REST API: list, fetch as YAML, push, publish, unpublish.

A metaseed instance pushing a profile it authored has no draft and no browser,
only a token and a YAML document. A push lands as a **private draft** in the
caller's account -- the same thing the spec builder's Import page and the
``spec_import_yaml`` tool produce -- because on this hub *published* means
visible to every user, and that is a decision a person makes, not a side
effect of a push. Publishing is asked for explicitly (``publish: true``), and
goes through the same gate as the builder's publish: the newest published
version of the name in the caller's tenant decides what version bump the
content requires. Identical content is not a second release.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from metaseed.specs import content_hash
from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.access import get_tenant_for_user, live_user
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Spec, SpecDraft, SpecStatus, User
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from metaseed_hub.ui.spec_builder.versioning import bump_refusal, latest_published_spec
from metaseed_hub.ui.spec_builder_helpers import parse_spec_from_yaml, spec_to_yaml

router = APIRouter()

# A specification is a small document; an unbounded body hands memory to
# whatever the client sends (the Import page applies the same bound).
MAX_SPEC_BYTES = 2 * 1024 * 1024


class SpecPush(BaseModel):
    """A profile to push, as the YAML document metaseed keeps on disk."""

    yaml: str
    publish: bool = False
    """Publish it for every hub user. Off, the push is a private draft."""


class SpecSummary(BaseModel):
    """One specification the caller can see, enough to decide whether to pull it."""

    id: str
    name: str
    version: str
    description: str | None
    content_hash: str | None
    tenant_id: str
    visibility: str
    """``draft`` (the caller's own, private) or ``published`` (every hub user)."""
    mine: bool
    """Whether the caller's account holds it."""


def _published() -> Select[tuple[Spec]]:
    return select(Spec).where(Spec.status == SpecStatus.PUBLISHED, Spec.deleted_at.is_(None))


async def _caller(session: AsyncSession, user: TokenUser) -> User:
    """The caller's account row.

    Raises:
        HTTPException: 403 when the token's account is not on this hub.
    """
    row = await live_user(session, user)
    if row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No hub account")
    return row


def _draft_spec(draft: SpecDraft) -> Any:
    return SpecBuilderState.from_dict(draft.spec_data).spec if draft.spec_data else None


def _draft_summary(draft: SpecDraft) -> SpecSummary:
    spec = _draft_spec(draft)
    return SpecSummary(
        id=draft.id,
        name=draft.name,
        version=draft.version,
        description=spec.description if spec is not None else None,
        content_hash=content_hash(spec) if spec is not None else None,
        tenant_id=draft.tenant_id,
        visibility="draft",
        mine=True,
    )


def _spec_summary(spec: Spec, tenant_id: str | None) -> SpecSummary:
    return SpecSummary(
        id=spec.id,
        name=spec.name,
        version=spec.version,
        description=spec.description,
        content_hash=spec.content_hash,
        tenant_id=spec.tenant_id,
        visibility="published",
        mine=spec.tenant_id == tenant_id,
    )


@router.get("", response_model=list[SpecSummary])
async def list_specs(
    user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SpecSummary]:
    """The caller's own drafts, then every published specification."""
    caller = await _caller(session, user)
    drafts = (
        await session.execute(
            select(SpecDraft)
            .where(SpecDraft.user_id == caller.id)
            .order_by(SpecDraft.name, SpecDraft.version)
        )
    ).scalars()
    published = (await session.execute(_published().order_by(Spec.name, Spec.version))).scalars()
    return [_draft_summary(d) for d in drafts] + [
        _spec_summary(s, caller.tenant_id) for s in published
    ]


@router.get("/{name}/{version}")
async def get_spec(
    name: str,
    version: str,
    user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """A specification as its YAML profile document.

    The caller's own draft of that name and version wins, then a published
    one from the caller's tenant, then any published one.

    Raises:
        HTTPException: 404 when nothing the caller can see matches.
    """
    caller = await _caller(session, user)
    draft = (
        await session.execute(
            select(SpecDraft).where(
                SpecDraft.user_id == caller.id,
                SpecDraft.name == name,
                SpecDraft.version == version,
            )
        )
    ).scalar_one_or_none()
    spec_data = draft.spec_data if draft is not None else None
    if not spec_data:
        rows = list(
            (
                await session.execute(
                    _published().where(Spec.name == name, Spec.version == version)
                )
            ).scalars()
        )
        own = [r for r in rows if r.tenant_id == caller.tenant_id]
        candidates = own or rows
        if candidates:
            spec_data = candidates[0].spec_data
    spec = SpecBuilderState.from_dict(spec_data).spec if spec_data else None
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Specification not found")
    return Response(content=spec_to_yaml(spec), media_type="application/x-yaml")


def _parse(push: SpecPush) -> Any:
    if len(push.yaml.encode()) > MAX_SPEC_BYTES:
        raise HTTPException(status_code=413, detail="Specification exceeds 2 MB")
    try:
        spec = parse_spec_from_yaml(push.yaml)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Not a profile document: {exc}",
        ) from exc
    if not spec.name or not spec.version:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A profile needs a name and a version before it can be pushed",
        )
    return spec


async def _push_draft(
    session: AsyncSession, caller: User, spec: Any, response: Response
) -> SpecSummary:
    """Create the caller's private draft of ``spec``, or update it in place."""
    digest = content_hash(spec)
    state = SpecBuilderState()
    state.spec = spec
    # By name alone: draft names are unique per (tenant, user), so matching on
    # the version as well missed the existing draft when a push bumped it, and
    # the insert below then hit the unique index as an unhandled 500.
    draft = (
        await session.execute(
            select(SpecDraft).where(
                SpecDraft.user_id == caller.id,
                SpecDraft.name == spec.name,
            )
        )
    ).scalar_one_or_none()
    if draft is not None:
        current = _draft_spec(draft)
        if (
            current is not None
            and draft.version == spec.version
            and content_hash(current) == digest
        ):
            response.status_code = status.HTTP_200_OK
            return _draft_summary(draft)
        draft.version = spec.version
        draft.spec_data = state.to_dict()
        await session.commit()
        await session.refresh(draft)
        response.status_code = status.HTTP_200_OK
        return _draft_summary(draft)
    draft = SpecDraft(
        user_id=caller.id,
        tenant_id=caller.tenant_id,
        name=spec.name,
        version=spec.version,
        spec_data=state.to_dict(),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    response.status_code = status.HTTP_201_CREATED
    return _draft_summary(draft)


async def _publish(
    session: AsyncSession, user: TokenUser, caller: User, spec: Any, response: Response
) -> SpecSummary:
    """Publish ``spec`` for every hub user, under the version-bump gate."""
    tenant = await get_tenant_for_user(session, user)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No hub account")
    digest = content_hash(spec)
    existing = await session.execute(
        _published().where(
            Spec.tenant_id == tenant.id, Spec.name == spec.name, Spec.version == spec.version
        )
    )
    same: Spec | None = existing.scalar_one_or_none()
    if same is not None:
        if same.content_hash == digest:
            response.status_code = status.HTTP_200_OK
            return _spec_summary(same, tenant.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"'{spec.name}' {spec.version} is already published with different "
                f"content. Bump the version and push again."
            ),
        )
    previous = await latest_published_spec(session, tenant_id=tenant.id, name=spec.name)
    if previous is not None and previous.spec_data:
        previous_spec = SpecBuilderState.from_dict(previous.spec_data).spec
        refusal = bump_refusal(previous_spec, spec) if previous_spec is not None else None
        if refusal is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal.message)
    state = SpecBuilderState()
    state.spec = spec
    row = Spec(
        tenant_id=tenant.id,
        name=spec.name,
        version=spec.version,
        description=spec.description,
        spec_data=state.to_dict(),
        content_hash=digest,
        status=SpecStatus.PUBLISHED,
        created_by_id=caller.id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{spec.name}' {spec.version} was published meanwhile",
        ) from exc
    await session.refresh(row)
    response.status_code = status.HTTP_201_CREATED
    return _spec_summary(row, tenant.id)


@router.post("", response_model=SpecSummary)
async def push_spec(
    push: SpecPush,
    response: Response,
    user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpecSummary:
    """Push a profile document into the caller's account.

    Without ``publish``, it becomes -- or replaces -- the caller's private
    draft of that name and version: only the caller sees it, and pushing a
    revised profile updates it. With ``publish``, it is published for every
    hub user under the version-bump gate.

    Returns 201 with what was created, or 200 when the account already held
    exactly this content.

    Raises:
        HTTPException: 422 when the document is not a profile; 409 when
            publishing is refused by the gate; 403 when the account is not
            on this hub.
    """
    spec = _parse(push)
    caller = await _caller(session, user)
    if push.publish:
        return await _publish(session, user, caller, spec, response)
    return await _push_draft(session, caller, spec, response)


@router.post("/{spec_id}/unpublish", response_model=SpecSummary)
async def unpublish(
    spec_id: str,
    user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpecSummary:
    """Withdraw a published specification back into a private draft.

    The same people who may edit a specification may withdraw it -- its
    publisher, and the tenant's admins and owners -- and one that datasets
    are built on cannot be withdrawn while they depend on it.

    Raises:
        HTTPException: 404 when there is no such published specification;
            403 when the caller may not withdraw it; 409 when datasets use it.
    """
    from metaseed_hub.ui.spec_builder.access import (
        SpecInUseError,
        can_edit_spec,
        unpublish_spec,
    )

    caller = await _caller(session, user)
    spec = (await session.execute(_published().where(Spec.id == spec_id))).scalar_one_or_none()
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Specification not found")
    if not await can_edit_spec(session, caller.id, spec.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You may not withdraw this specification"
        )
    try:
        draft = await unpublish_spec(session, spec, caller.id)
    except SpecInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _draft_summary(draft)
