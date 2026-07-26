"""Dataset CRUD API endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from metaseed.repositories import AsyncDatasetRepository
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import get_tenant_for_user, verify_tenant_access

router = APIRouter()


async def _get_owned_dataset(dataset_id: str, session: AsyncSession, user: TokenUser) -> Dataset:
    """Fetch a non-deleted dataset owned by the caller's tenant.

    Args:
        dataset_id: Dataset identifier.
        session: Database session.
        user: Authenticated user.

    Returns:
        The dataset if it exists, is not soft-deleted, and belongs to the
        caller's tenant.

    Raises:
        HTTPException: 404 if no such dataset is visible to the caller. A 404
            (rather than 403) is used so dataset existence is not disclosed
            across tenants.
    """
    tenant = await get_tenant_for_user(session, user)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    result = await session.execute(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.tenant_id == tenant.id,
            Dataset.deleted_at.is_(None),
        )
    )
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    return dataset


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
    session.add(dataset)
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
    return await _get_owned_dataset(dataset_id, session, _user)


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
    dataset = await _get_owned_dataset(dataset_id, session, _user)

    if dataset_data.name is not None:
        name_error = AsyncDatasetRepository.validate_name(dataset_data.name)
        if name_error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=name_error)
        dataset.name = dataset_data.name
    if dataset_data.data is not None:
        dataset.data = dataset_data.data

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
    dataset = await _get_owned_dataset(dataset_id, session, _user)
    dataset.soft_delete()
    await session.commit()
