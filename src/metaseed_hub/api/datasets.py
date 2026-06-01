"""Dataset CRUD API endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset

router = APIRouter()


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
    """
    result = await session.execute(select(Dataset).where(Dataset.tenant_id == tenant_id))
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
    """
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
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    return dataset


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
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )

    if dataset_data.name is not None:
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
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )

    await session.delete(dataset)
    await session.commit()
