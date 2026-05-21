"""Workspace routes for Hub UI."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import (
    Dataset,
    SpecDraft,
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from metaseed_hub.ui.dependencies import CurrentUser, DbSession, verify_workspace_access
from metaseed_hub.ui.helpers import validate_csrf_token
from metaseed_hub.ui.render import init_templates as _init_render_templates
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def init_templates(templates: Jinja2Templates) -> None:
    """Initialize templates reference."""
    _init_render_templates(templates)


async def _get_or_create_tenant(session: AsyncSession, user: TokenUser) -> Tenant:
    """Get or create tenant for user based on keycloak_id."""
    slug = user.keycloak_id[:8]
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    tenant = result.scalar_one_or_none()
    if not tenant:
        tenant = Tenant(name=user.name or user.email, slug=slug)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
    return tenant


@router.get("/new", response_class=HTMLResponse)
async def workspace_new(
    request: Request,
    user: CurrentUser,
) -> Response:
    """Return workspace creation form."""
    return render_template(
        request=request,
        name="partials/workspace_form.html",
        context={"user": user, "nav_active": "home"},
    )


@router.post("")
async def workspace_create(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
) -> RedirectResponse:
    """Create a new workspace."""
    if not validate_csrf_token(request, csrf_token):
        return RedirectResponse("/hub/?error=csrf_validation_failed", status_code=302)
    # Note: Using redirect for form-based CSRF errors to preserve UX

    tenant = await _get_or_create_tenant(session, user)

    workspace = Workspace(
        tenant_id=tenant.id,
        name=name,
        description=description,
    )
    session.add(workspace)
    await session.commit()

    return RedirectResponse(f"/hub/workspaces/{workspace.id}", status_code=303)


@router.get("/{workspace_id}", response_class=HTMLResponse)
async def workspace_detail(
    request: Request,
    workspace_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Show datasets in a workspace."""
    # Verify user has access to this workspace
    workspace = await verify_workspace_access(workspace_id, session, user)

    result = await session.execute(select(Dataset).where(Dataset.workspace_id == workspace_id))
    datasets = list(result.scalars().all())

    # Get spec drafts for this workspace
    drafts_result = await session.execute(
        select(SpecDraft).where(SpecDraft.workspace_id == workspace_id)
    )
    spec_drafts = list(drafts_result.scalars().all())

    # Get workspace members
    members_result = await session.execute(
        select(WorkspaceMember, User)
        .join(User, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    members = [(m, u) for m, u in members_result.all()]

    return render_template(
        request=request,
        name="workspace.html",
        context={
            "user": user,
            "workspace": workspace,
            "datasets": datasets,
            "spec_drafts": spec_drafts,
            "members": members,
            "nav_active": "home",
        },
    )


@router.get("/{workspace_id}/edit", response_class=HTMLResponse)
async def workspace_edit_form(
    request: Request,
    workspace_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return workspace edit form."""
    # Verify user has access to this workspace
    workspace = await verify_workspace_access(workspace_id, session, user)

    return render_template(
        request=request,
        name="partials/workspace_form.html",
        context={
            "user": user,
            "workspace": workspace,
            "nav_active": "home",
        },
    )


@router.put("/{workspace_id}", response_class=HTMLResponse)
async def workspace_update(
    request: Request,
    workspace_id: str,
    session: DbSession,
    user: CurrentUser,
    name: Annotated[str, Form()],
    description: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
) -> Response:
    """Update a workspace."""
    try:
        validate_csrf_or_error(request, csrf_token)
    except Exception:
        return csrf_error_response()

    # Verify user has access to this workspace
    workspace = await verify_workspace_access(workspace_id, session, user)

    workspace.name = name
    workspace.description = description
    session.add(workspace)
    await session.commit()

    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/workspaces/{workspace_id}"
    return response


@router.delete("/{workspace_id}", response_class=HTMLResponse)
async def workspace_delete(
    request: Request,
    workspace_id: str,
    session: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    """Delete a workspace and all its datasets."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Verify user has access to this workspace
    workspace = await verify_workspace_access(workspace_id, session, user)

    # Delete all datasets in workspace first
    datasets = (
        (await session.execute(select(Dataset).where(Dataset.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    for dataset in datasets:
        await session.delete(dataset)

    await session.delete(workspace)
    await session.commit()

    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = "/hub/"
    return response


@router.post("/{workspace_id}/members", response_class=HTMLResponse)
async def add_member(
    request: Request,
    workspace_id: str,
    session: DbSession,
    user: CurrentUser,
    email: Annotated[str, Form()],
    role: Annotated[str, Form()] = "editor",
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
) -> Response:
    """Add a member to the workspace by email."""
    try:
        validate_csrf_or_error(request, csrf_token)
    except Exception:
        return csrf_error_response()

    await verify_workspace_access(workspace_id, session, user)
    tenant = await _get_or_create_tenant(session, user)

    # Find or create user by email
    result = await session.execute(select(User).where(User.email == email))
    member_user = result.scalar_one_or_none()

    if not member_user:
        # Create user record - they'll be linked when they log in via Keycloak
        member_user = User(
            keycloak_id=f"pending:{email}",
            email=email,
            display_name=email.split("@")[0],
            tenant_id=tenant.id,
        )
        session.add(member_user)
        await session.flush()

    # Check if already a member
    existing = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == member_user.id,
        )
    )
    if existing.scalar_one_or_none():
        response = HTMLResponse(status_code=200)
        response.headers["HX-Redirect"] = f"/hub/workspaces/{workspace_id}"
        return response

    # Add membership
    ws_role = WorkspaceRole.OWNER if role == "owner" else WorkspaceRole.EDITOR
    membership = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=member_user.id,
        role=ws_role,
    )
    session.add(membership)
    await session.commit()

    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/workspaces/{workspace_id}"
    return response


@router.delete("/{workspace_id}/members/{member_id}", response_class=HTMLResponse)
async def remove_member(
    request: Request,
    workspace_id: str,
    member_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Remove a member from the workspace."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    await verify_workspace_access(workspace_id, session, user)

    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == member_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        await session.delete(membership)
        await session.commit()

    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/workspaces/{workspace_id}"
    return response
