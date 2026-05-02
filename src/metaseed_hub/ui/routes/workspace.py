"""Workspace routes for Hub UI."""

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Project, Tenant, Workspace
from metaseed_hub.ui.dependencies import CurrentUser, DbSession
from metaseed_hub.ui.helpers import (
    CSRF_TOKEN_COOKIE,
    get_or_create_csrf_token,
    validate_csrf_token,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Templates reference, initialized by init_templates()
_templates: Jinja2Templates | None = None


def init_templates(templates: Jinja2Templates) -> None:
    """Initialize templates reference."""
    global _templates
    _templates = templates


def _render_template(
    request: Request,
    name: str,
    context: dict[str, Any],
    status_code: int = 200,
) -> Response:
    """Render template with CSRF token included.

    Automatically adds CSRF token to context and sets cookie.
    """
    if _templates is None:
        raise RuntimeError("Templates not initialized. Call init_templates() first.")

    csrf_token = get_or_create_csrf_token(request)
    context["csrf_token"] = csrf_token
    context["request"] = request

    response = _templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )

    # Set CSRF cookie if not already set
    if not request.cookies.get(CSRF_TOKEN_COOKIE):
        response.set_cookie(
            key=CSRF_TOKEN_COOKIE,
            value=csrf_token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=3600 * 24,  # 24 hours
        )

    return response


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
    return _render_template(
        request=request,
        name="partials/workspace_form.html",
        context={"user": user},
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
    """Show projects in a workspace."""
    result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()

    if not workspace:
        return RedirectResponse("/hub/")

    result = await session.execute(select(Project).where(Project.workspace_id == workspace_id))
    projects = list(result.scalars().all())

    return _render_template(
        request=request,
        name="workspace.html",
        context={
            "user": user,
            "workspace": workspace,
            "projects": projects,
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
    result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        return HTMLResponse("<div class='error'>Workspace not found</div>")

    return _render_template(
        request=request,
        name="partials/workspace_form.html",
        context={
            "user": user,
            "workspace": workspace,
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
    if not validate_csrf_token(request, csrf_token):
        return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

    result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        return HTMLResponse("<div class='error'>Workspace not found</div>")

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
    """Delete a workspace and all its projects."""
    if not validate_csrf_token(request):
        return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

    result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        return HTMLResponse("<div class='error'>Workspace not found</div>")

    # Delete all projects in workspace first
    projects = (
        (await session.execute(select(Project).where(Project.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    for project in projects:
        await session.delete(project)

    await session.delete(workspace)
    await session.commit()

    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = "/hub/"
    return response
