"""Dataset CRUD API endpoints."""

import copy
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from metaseed.repositories import AsyncDatasetRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.access import (
    get_dataset_for_editor,
    get_dataset_for_user,
    live_user,
    require_dataset_owner,
    verify_tenant_access,
)
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset
from metaseed_hub.sharing import record_creator, resource_for

router = APIRouter()


async def _shared(
    access_helper: "Callable[[str, AsyncSession, TokenUser], Awaitable[Dataset]]",
    dataset_id: str,
    session: AsyncSession,
    user: TokenUser,
) -> Dataset:
    """Resolve a dataset through the shared access ladder, without disclosure.

    The API answered tenant-owned datasets only, ignoring the ``DatasetMember``
    sharing the UI and websocket honour — so a dataset a colleague shared was
    editable in the browser and a 404 over the same account's token. Access now
    goes through :mod:`metaseed_hub.access`, the one answer every layer gives.

    The ladder raises 403 for "exists, not yours", which across tenants would
    disclose that the id exists. This API's contract is that it does not, so a
    refusal to a non-member is folded into 404. A member refused for their
    *role* (a viewer PATCHing) already knows the dataset exists, and keeps the
    403 that tells them why.

    Args:
        access_helper: One rung of the ladder (for_user / for_editor / owner).
        dataset_id: Dataset identifier.
        session: Database session.
        user: Authenticated user.

    Returns:
        The dataset, if the rung admits this caller.

    Raises:
        HTTPException: 404 for missing or not visible; 403 for a member whose
            role does not reach the rung.
    """
    try:
        return await access_helper(dataset_id, session, user)
    except HTTPException as exc:
        if exc.status_code == 403:
            # Distinguish "no access at all" (hide) from "member, wrong role"
            # (explain): only the latter passes the viewer rung.
            try:
                await get_dataset_for_user(dataset_id, session, user)
            except HTTPException:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found"
                ) from exc
            raise
        raise


class DatasetCreate(BaseModel):
    """Schema for creating a dataset."""

    tenant_id: str
    name: str
    profile: str
    version: str
    data: dict[str, Any] = {}


class DatasetUpdate(BaseModel):
    """Schema for updating a dataset."""

    name: str | None = None
    data: dict[str, Any] | None = None


class DatasetResponse(BaseModel):
    """Schema for dataset responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    profile: str
    version: str
    data: dict[str, Any]


async def _validated_data(
    dataset: Dataset, data: dict[str, Any], session: AsyncSession
) -> dict[str, Any]:
    """The payload, once it is known to load under the dataset's profile.

    Every UI mutation goes through the write load path, which refuses a payload
    whose nodes the profile cannot place: saving serializes the loaded facade,
    so anything that did not load is deleted by the next save. This route wrote
    the payload straight in, so an API client could store a dataset the UI then
    silently truncates.

    Args:
        dataset: The dataset being patched, for its profile and spec ids.
        data: The proposed payload.
        session: Database session, for spec drafts.

    Returns:
        ``data`` unchanged, when it loads.

    Raises:
        HTTPException: 409 when a node cannot be placed, 422 when the payload
            is not loadable at all.
    """
    from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade_for_write

    # A fresh transient row, not a copy of ``dataset``: copying a mapped
    # instance shares its ORM state, which on a row not yet persisted left the
    # session unable to refresh it after the insert. The load also rewrites
    # nested dicts into model objects in place (an ISA ontology source, a
    # term), which on the caller's dict left a payload the database could not
    # store as JSON -- so it works on a deep copy and the original is kept.
    proposed = Dataset(
        tenant_id=dataset.tenant_id,
        profile=dataset.profile,
        version=dataset.version,
        spec_id=dataset.spec_id,
        spec_draft_id=dataset.spec_draft_id,
        data=copy.deepcopy(data),
    )
    try:
        await ensure_dataset_facade_for_write(proposed, session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Dataset payload could not be loaded: {exc}",
        ) from exc
    return data


@router.get("", response_model=list[DatasetResponse])
async def list_datasets(
    tenant_id: str,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Dataset]:
    """List all datasets in a tenant.

    Args:
        tenant_id: Tenant to list datasets from.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        List of datasets in the tenant.

    Raises:
        HTTPException: If the caller may not access the requested tenant.
    """
    await verify_tenant_access(tenant_id, session, _user)
    result = await session.execute(
        select(Dataset).where(
            Dataset.tenant_id == tenant_id,
            Dataset.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    dataset_data: DatasetCreate,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dataset:
    """Create a new dataset.

    Args:
        dataset_data: Dataset creation data.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        Created dataset.

    Raises:
        HTTPException: If the caller may not create datasets in the tenant.
    """
    await verify_tenant_access(dataset_data.tenant_id, session, _user)

    # Enforce the same name rule the repository save() path applies, so the REST
    # API cannot create datasets with names the UI path would reject.
    name_error = AsyncDatasetRepository.validate_name(dataset_data.name)
    if name_error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=name_error)

    dataset = Dataset(
        tenant_id=dataset_data.tenant_id,
        name=dataset_data.name,
        profile=dataset_data.profile,
        version=dataset_data.version,
        data=dataset_data.data,
    )
    # The same check PATCH applies: a payload the profile cannot place -- or a
    # profile this hub does not have -- must not become a row the UI then
    # truncates or cannot open. A metaseed instance pushing a dataset built on
    # a profile it has not pushed yet gets the refusal, not a broken record.
    if dataset_data.data:
        dataset.data = await _validated_data(dataset, dataset_data.data, session)
    session.add(dataset)
    creator = await live_user(session, _user)
    await record_creator(session, resource_for("dataset"), dataset, creator.id if creator else None)
    await session.commit()
    await session.refresh(dataset)
    return dataset


@router.get("/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(
    dataset_id: str,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dataset:
    """Get a dataset by ID.

    Args:
        dataset_id: Dataset identifier.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        Dataset if found.

    Raises:
        HTTPException: If dataset not found.
    """
    return await _shared(get_dataset_for_user, dataset_id, session, _user)


@router.patch("/{dataset_id}", response_model=DatasetResponse)
async def update_dataset(
    dataset_id: str,
    dataset_data: DatasetUpdate,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Dataset:
    """Update a dataset.

    Args:
        dataset_id: Dataset identifier.
        dataset_data: Fields to update.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        Updated dataset.

    Raises:
        HTTPException: If dataset not found.
    """
    dataset = await _shared(get_dataset_for_editor, dataset_id, session, _user)

    if dataset_data.name is not None:
        name_error = AsyncDatasetRepository.validate_name(dataset_data.name)
        if name_error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=name_error)
        dataset.name = dataset_data.name
    if dataset_data.data is not None:
        dataset.data = await _validated_data(dataset, dataset_data.data, session)

    await session.commit()
    await session.refresh(dataset)
    return dataset


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: str,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a dataset.

    Args:
        dataset_id: Dataset identifier.
        _user: Current authenticated user.
        session: Database session.

    Raises:
        HTTPException: If dataset not found.
    """
    dataset = await _shared(require_dataset_owner, dataset_id, session, _user)
    dataset.soft_delete()
    await session.commit()
