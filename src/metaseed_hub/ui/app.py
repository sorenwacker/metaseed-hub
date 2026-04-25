"""Hub UI application that extends metaseed's HTMX interface.

Adds authentication, project management, and collaboration features
on top of the base metaseed entity editing UI.
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from metaseed.ui.state import AppState
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, get_current_user_optional
from metaseed_hub.database import get_session
from metaseed_hub.models import Project, Workspace

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


def create_hub_app() -> FastAPI:
    """Create the Hub FastAPI application with extended UI.

    Returns:
        FastAPI application with hub routes and mounted metaseed UI.
    """
    app = FastAPI(title="Metaseed Hub")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Mount hub static files
    app.mount("/hub-static", StaticFiles(directory=str(STATIC_DIR)), name="hub-static")

    # Store for project-specific metaseed app states
    project_states: dict[str, AppState] = {}

    def get_project_state(project_id: str) -> AppState:
        """Get or create AppState for a project."""
        if project_id not in project_states:
            project_states[project_id] = AppState()
        return project_states[project_id]

    @app.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        user: Annotated[TokenUser | None, Depends(get_current_user_optional)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Home page - show workspaces and projects."""
        if not user:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"request": request},
            )

        # Get user's workspaces
        result = await session.execute(
            select(Workspace).where(Workspace.tenant_id == user.tenant_id)
        )
        workspaces = list(result.scalars().all())

        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "request": request,
                "user": user,
                "workspaces": workspaces,
            },
        )

    @app.get("/workspaces/{workspace_id}", response_class=HTMLResponse)
    async def workspace_detail(
        request: Request,
        workspace_id: str,
        user: Annotated[TokenUser | None, Depends(get_current_user_optional)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Show projects in a workspace."""
        if not user:
            return RedirectResponse("/")

        result = await session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = result.scalar_one_or_none()

        if not workspace:
            return RedirectResponse("/")

        result = await session.execute(
            select(Project).where(Project.workspace_id == workspace_id)
        )
        projects = list(result.scalars().all())

        return templates.TemplateResponse(
            request=request,
            name="workspace.html",
            context={
                "request": request,
                "user": user,
                "workspace": workspace,
                "projects": projects,
            },
        )

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_editor(
        request: Request,
        project_id: str,
        user: Annotated[TokenUser | None, Depends(get_current_user_optional)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Project editor - wraps metaseed UI with hub chrome."""
        if not user:
            return RedirectResponse("/")

        result = await session.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()

        if not project:
            return RedirectResponse("/")

        # Get or create state for this project
        state = get_project_state(project_id)
        state.profile = project.profile
        state.version = project.version

        return templates.TemplateResponse(
            request=request,
            name="project.html",
            context={
                "request": request,
                "user": user,
                "project": project,
                "state": state,
                "root_types": state.get_root_entity_types(),
            },
        )

    # Mount metaseed UI for project-specific editing
    # Each project gets its own state via the project_id parameter
    @app.get("/projects/{project_id}/edit", response_class=HTMLResponse)
    async def project_metaseed_ui(
        request: Request,
        project_id: str,
        user: Annotated[TokenUser | None, Depends(get_current_user_optional)],
    ) -> HTMLResponse:
        """Embedded metaseed UI for a project."""
        if not user:
            return RedirectResponse("/")

        state = get_project_state(project_id)

        # Forward the request to metaseed's index
        # This is a simplified approach - in production you'd use proper sub-app mounting
        from metaseed.ui.app import TEMPLATES_DIR as METASEED_TEMPLATES

        metaseed_templates = Jinja2Templates(directory=str(METASEED_TEMPLATES))

        return metaseed_templates.TemplateResponse(
            request=request,
            name="base.html",
            context={
                "request": request,
                "root_types": state.get_root_entity_types(),
                "tree_data": state.get_tree_data(),
            },
        )

    return app
