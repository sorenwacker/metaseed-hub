"""Published specifications over the REST API: list, fetch as YAML, publish.

The spec builder publishes a draft through the browser; a metaseed instance
pushing a profile it authored has no draft and no browser, only a token and a
YAML document. Publishing here goes through the same gate as the builder's
publish: the newest published version of the name in the caller's tenant
decides what version bump the content requires, and a document that declares
less is refused with the reason. Identical content is not a second release.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from metaseed.specs import content_hash
from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.access import get_tenant_for_user
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Spec, SpecStatus, User
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from metaseed_hub.ui.spec_builder.versioning import bump_refusal, latest_published_spec
from metaseed_hub.ui.spec_builder_helpers import parse_spec_from_yaml, spec_to_yaml

router = APIRouter()

# A specification is a small document; an unbounded body hands memory to
# whatever the client sends (the Import page applies the same bound).
MAX_SPEC_BYTES = 2 * 1024 * 1024


class SpecPush(BaseModel):
    """A profile to publish, as the YAML document metaseed keeps on disk."""

    yaml: str


class SpecSummary(BaseModel):
    """One published specification, enough to decide whether to pull it."""

    id: str
    name: str
    version: str
    description: str | None
    content_hash: str | None
    tenant_id: str


def _published() -> Select[tuple[Spec]]:
    return select(Spec).where(Spec.status == SpecStatus.PUBLISHED, Spec.deleted_at.is_(None))


@router.get("", response_model=list[SpecSummary])
async def list_specs(
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Spec]:
    """Every published specification, as ``list_profiles`` shows them."""
    result = await session.execute(_published().order_by(Spec.name, Spec.version))
    return list(result.scalars().all())


@router.get("/{name}/{version}")
async def get_spec(
    name: str,
    version: str,
    user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """A published specification as the YAML profile document.

    Where two tenants published the same name and version, the caller's own
    tenant's wins, as it does when a dataset is built against it.

    Raises:
        HTTPException: 404 when nothing published matches.
    """
    result = await session.execute(_published().where(Spec.name == name, Spec.version == version))
    rows = list(result.scalars().all())
    tenant = await get_tenant_for_user(session, user)
    own = [r for r in rows if tenant is not None and r.tenant_id == tenant.id]
    candidates = own or rows
    row = candidates[0] if candidates else None
    if row is None or not row.spec_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Specification not found")
    spec = SpecBuilderState.from_dict(row.spec_data).spec
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Specification not found")
    return Response(content=spec_to_yaml(spec), media_type="application/x-yaml")


@router.post("", response_model=SpecSummary)
async def publish_spec(
    push: SpecPush,
    response: Response,
    user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Spec:
    """Publish a profile document in the caller's tenant.

    Returns 201 with the new specification, or 200 with the existing one when
    the document's content is already published at that name and version.

    Raises:
        HTTPException: 422 when the document is not a profile; 409 when the
            declared version is below what the change from the latest
            published version requires, or is already taken by different
            content; 403 when the account has no tenant.
    """
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
            detail="A profile needs a name and a version before it can be published",
        )
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
            return same
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
    creator = (
        await session.execute(
            select(User).where(User.keycloak_id == user.sub, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
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
        created_by_id=creator.id if creator is not None else None,
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
    return row
