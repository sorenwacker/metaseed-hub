"""Project CRUD API endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import Project

router = APIRouter()


class ProjectCreate(BaseModel):
    """Schema for creating a project."""

    workspace_id: str
    name: str
    profile: str
    version: str
    data: dict[str, Any] = {}


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""

    name: str | None = None
    data: dict[str, Any] | None = None


class ProjectResponse(BaseModel):
    """Schema for project responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    name: str
    profile: str
    version: str
    data: dict[str, Any]


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    workspace_id: str,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Project]:
    """List all projects in a workspace.

    Args:
        workspace_id: Workspace to list projects from.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        List of projects in the workspace.
    """
    result = await session.execute(
        select(Project).where(Project.workspace_id == workspace_id)
    )
    return list(result.scalars().all())


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Project:
    """Create a new project.

    Args:
        project_data: Project creation data.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        Created project.
    """
    project = Project(
        workspace_id=project_data.workspace_id,
        name=project_data.name,
        profile=project_data.profile,
        version=project_data.version,
        data=project_data.data,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Project:
    """Get a project by ID.

    Args:
        project_id: Project identifier.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        Project if found.

    Raises:
        HTTPException: If project not found.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    project_data: ProjectUpdate,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Project:
    """Update a project.

    Args:
        project_id: Project identifier.
        project_data: Fields to update.
        _user: Current authenticated user.
        session: Database session.

    Returns:
        Updated project.

    Raises:
        HTTPException: If project not found.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if project_data.name is not None:
        project.name = project_data.name
    if project_data.data is not None:
        project.data = project_data.data

    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    _user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Delete a project.

    Args:
        project_id: Project identifier.
        _user: Current authenticated user.
        session: Database session.

    Raises:
        HTTPException: If project not found.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    await session.delete(project)
    await session.commit()
