"""Hub UI application that extends metaseed's HTMX interface.

Adds authentication, project management, and collaboration features
on top of the base metaseed entity editing UI.
"""

import secrets
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from metaseed.specs.loader import SpecLoader
from metaseed.ui.state import AppState
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser, verify_token
from metaseed_hub.config import get_settings
from metaseed_hub.database import get_session
from metaseed_hub.models import Project, Tenant, Workspace
from metaseed_hub.ui.spec_builder_routes import create_spec_builder_router

UI_DIR = Path(__file__).parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"

ACCESS_TOKEN_COOKIE = "metaseed_access_token"
STATE_COOKIE = "metaseed_oauth_state"


async def get_current_user_from_cookie(request: Request) -> TokenUser | None:
    """Extract and verify user from access token cookie."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        return await verify_token(token)
    except Exception:
        return None


def create_hub_app() -> FastAPI:
    """Create the Hub FastAPI application with extended UI.

    Returns:
        FastAPI application with hub routes and mounted metaseed UI.
    """
    app = FastAPI(title="Metaseed Hub")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Mount hub static files
    app.mount("/hub-static", StaticFiles(directory=str(STATIC_DIR)), name="hub-static")

    # Add spec builder routes
    spec_builder_router = create_spec_builder_router(templates)
    app.include_router(spec_builder_router)

    # Store for project-specific metaseed app states
    project_states: dict[str, AppState] = {}

    def get_project_state(project_id: str) -> AppState:
        """Get or create AppState for a project."""
        if project_id not in project_states:
            project_states[project_id] = AppState()
        return project_states[project_id]

    settings = get_settings()
    hub_base_url = f"{settings.app_url}/hub"

    @app.get("/auth/login")
    async def auth_login(request: Request) -> RedirectResponse:
        """Redirect to Keycloak login page."""
        state = secrets.token_urlsafe(32)
        redirect_uri = f"{hub_base_url}/auth/callback"

        params = {
            "client_id": settings.keycloak_client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
        }

        auth_url = f"{settings.keycloak_issuer}/protocol/openid-connect/auth?{urlencode(params)}"

        response = RedirectResponse(url=auth_url, status_code=302)
        response.set_cookie(
            key=STATE_COOKIE,
            value=state,
            httponly=True,
            secure=False,  # Set True in production
            max_age=600,
        )
        return response

    @app.get("/auth/callback")
    async def auth_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        """Handle OAuth callback from Keycloak."""
        if error:
            return RedirectResponse(url="/hub/?error=auth_failed", status_code=302)

        stored_state = request.cookies.get(STATE_COOKIE)
        if not state or state != stored_state:
            return RedirectResponse(url="/hub/?error=invalid_state", status_code=302)

        if not code:
            return RedirectResponse(url="/hub/?error=no_code", status_code=302)

        # Exchange code for tokens
        redirect_uri = f"{hub_base_url}/auth/callback"

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                settings.keycloak_token_url,
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.keycloak_client_id,
                    "client_secret": settings.keycloak_client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )

        if token_response.status_code != 200:
            return RedirectResponse(url="/hub/?error=token_exchange_failed", status_code=302)

        tokens = token_response.json()
        access_token = tokens.get("access_token")

        response = RedirectResponse(url="/hub/", status_code=302)
        response.delete_cookie(key=STATE_COOKIE)
        response.set_cookie(
            key=ACCESS_TOKEN_COOKIE,
            value=access_token,
            httponly=True,
            secure=False,  # Set True in production
            max_age=tokens.get("expires_in", 3600),
            path="/",
        )
        return response

    @app.get("/auth/logout")
    async def auth_logout(request: Request) -> RedirectResponse:
        """Logout and redirect to Keycloak logout."""
        logout_url = f"{settings.keycloak_issuer}/protocol/openid-connect/logout"
        params = {
            "client_id": settings.keycloak_client_id,
            "post_logout_redirect_uri": hub_base_url,
        }

        response = RedirectResponse(url=f"{logout_url}?{urlencode(params)}", status_code=302)
        response.delete_cookie(key=ACCESS_TOKEN_COOKIE)
        return response

    @app.get("/privacy", response_class=HTMLResponse)
    async def privacy_policy(request: Request) -> HTMLResponse:
        """Privacy policy page."""
        user = await get_current_user_from_cookie(request)
        return templates.TemplateResponse(
            request=request,
            name="privacy.html",
            context={"request": request, "user": user},
        )

    @app.get("/aup", response_class=HTMLResponse)
    async def acceptable_use_policy(request: Request) -> HTMLResponse:
        """Acceptable use policy page."""
        user = await get_current_user_from_cookie(request)
        return templates.TemplateResponse(
            request=request,
            name="aup.html",
            context={"request": request, "user": user},
        )

    @app.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Home page - show workspaces and projects."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"request": request},
            )

        # Get or create tenant for user
        tenant = await get_or_create_tenant(session, user)

        # Get user's workspaces
        result = await session.execute(select(Workspace).where(Workspace.tenant_id == tenant.id))
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

    @app.get("/workspaces/new", response_class=HTMLResponse)
    async def workspace_new(request: Request) -> HTMLResponse:
        """Return workspace creation form."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        return templates.TemplateResponse(
            request=request,
            name="partials/workspace_form.html",
            context={"request": request},
        )

    @app.post("/workspaces")
    async def workspace_create(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        name: Annotated[str, Form()],
        description: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        """Create a new workspace."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse("/hub/", status_code=302)

        tenant = await get_or_create_tenant(session, user)

        workspace = Workspace(
            tenant_id=tenant.id,
            name=name,
            description=description,
        )
        session.add(workspace)
        await session.commit()

        return RedirectResponse(f"/hub/workspaces/{workspace.id}", status_code=303)

    @app.get("/workspaces/{workspace_id}", response_class=HTMLResponse)
    async def workspace_detail(
        request: Request,
        workspace_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Show projects in a workspace."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse("/hub/")

        result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        workspace = result.scalar_one_or_none()

        if not workspace:
            return RedirectResponse("/hub/")

        result = await session.execute(select(Project).where(Project.workspace_id == workspace_id))
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

    @app.get("/projects/new", response_class=HTMLResponse)
    async def project_new(
        request: Request,
        workspace_id: str,
    ) -> HTMLResponse:
        """Return project creation form."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        # Get available profiles and versions from metaseed
        loader = SpecLoader()
        profiles_data = []
        for profile_name in loader.list_profiles():
            versions = loader.list_versions(profile_name)
            profiles_data.append(
                {
                    "name": profile_name,
                    "versions": versions,
                }
            )

        return templates.TemplateResponse(
            request=request,
            name="partials/project_form.html",
            context={
                "request": request,
                "workspace_id": workspace_id,
                "profiles": profiles_data,
            },
        )

    @app.post("/projects")
    async def project_create(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        workspace_id: Annotated[str, Form()],
        name: Annotated[str, Form()],
        profile: Annotated[str, Form()],
        version: Annotated[str, Form()],
    ) -> RedirectResponse:
        """Create a new project."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse("/hub/", status_code=302)

        project = Project(
            workspace_id=workspace_id,
            name=name,
            profile=profile,
            version=version,
            data={},
        )
        session.add(project)
        await session.commit()

        return RedirectResponse(f"/hub/projects/{project.id}", status_code=303)

    @app.delete("/projects/{project_id}", response_class=HTMLResponse)
    async def project_delete(
        request: Request,
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Delete a project."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()

        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        workspace_id = project.workspace_id
        await session.delete(project)
        await session.commit()

        # Return updated project grid
        result = await session.execute(select(Project).where(Project.workspace_id == workspace_id))
        projects = list(result.scalars().all())

        html = '<div class="project-grid" id="project-grid">'
        for p in projects:
            html += f"""
            <div class="project-card">
                <a href="/hub/projects/{p.id}" class="project-card-link">
                    <h3>{p.name}</h3>
                    <div class="project-meta">
                        <span class="profile-badge">{p.profile} {p.version}</span>
                        <span class="date">{p.updated_at.strftime('%Y-%m-%d %H:%M')}</span>
                    </div>
                </a>
                <button class="project-delete" title="Delete project"
                        hx-delete="/hub/projects/{p.id}"
                        hx-target="#project-grid"
                        hx-swap="outerHTML"
                        hx-confirm="Delete '{p.name}'? This cannot be undone.">
                    &times;
                </button>
            </div>
            """
        if not projects:
            html += """
            <div class="empty-state">
                <p>No projects yet.</p>
                <p>Create a project to start working with metadata.</p>
            </div>
            """
        html += "</div>"
        return HTMLResponse(html)

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    async def project_editor(
        request: Request,
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Project editor - wraps metaseed UI with hub chrome."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse("/hub/")

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()

        if not project:
            return RedirectResponse("/hub/")

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

    @app.get("/projects/{project_id}/tree", response_class=HTMLResponse)
    async def project_tree(
        request: Request,
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Return entity tree for a project."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        state = get_project_state(project_id)
        tree_data = state.get_tree_data()

        if not tree_data:
            return HTMLResponse("<div class='empty-state'><p>No entities yet.</p></div>")

        # Render tree with entity types and actions
        html = "<ul class='entity-tree'>"
        for item in tree_data:
            item_id = item.get("id", "")
            item_name = item.get("name", "Unnamed")
            item_type = item.get("type", "Entity")
            html += f"""<li class='entity-item'>
                <span class='entity-type-badge'>{item_type}</span>
                <a href='#' class='entity-name'
                   hx-get='/hub/projects/{project_id}/entity/{item_id}'
                   hx-target='#editor'>{item_name}</a>
                <button class='entity-delete' title='Delete'
                        hx-delete='/hub/projects/{project_id}/entity/{item_id}'
                        hx-target='#entity-tree'
                        hx-confirm='Delete this {item_type}?'>x</button>
            </li>"""
        html += "</ul>"
        return HTMLResponse(html)

    @app.get("/projects/{project_id}/form/{entity_type}", response_class=HTMLResponse)
    async def project_entity_form(
        request: Request,
        project_id: str,
        entity_type: str,
        session: Annotated[AsyncSession, Depends(get_session)],
        node_id: str | None = None,
    ) -> HTMLResponse:
        """Return form for creating or editing an entity."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        # Load project to get profile/version
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project_id)
        state.profile = project.profile
        state.version = project.version
        facade = state.get_or_create_facade()

        # Get entity helper
        try:
            helper = getattr(facade, entity_type)
        except AttributeError:
            return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

        # Build field data for template
        fields = []
        for field_name in helper.all_fields:
            info = helper.field_info(field_name)
            fields.append(
                {
                    "name": field_name,
                    "type": info.get("type", "string"),
                    "required": info.get("required", False),
                    "description": info.get("description", ""),
                    "constraints": info.get("constraints"),
                    "item_type": info.get("items"),
                    "is_nested": info.get("type") in ("list", "entity")
                    and info.get("items") is not None,
                }
            )

        # Separate required, optional, and nested fields
        required_fields = [f for f in fields if f["required"] and not f["is_nested"]]
        optional_fields = [f for f in fields if not f["required"] and not f["is_nested"]]
        nested_fields = [f for f in fields if f["is_nested"]]

        # Get current values if editing
        values: dict[str, object] = {}
        if node_id and node_id in state.nodes_by_id:
            node = state.nodes_by_id[node_id]
            if hasattr(node.instance, "model_dump"):
                values = node.instance.model_dump(exclude_none=True)

        return templates.TemplateResponse(
            request=request,
            name="partials/entity_form.html",
            context={
                "request": request,
                "project_id": project_id,
                "entity_type": entity_type,
                "node_id": node_id,
                "description": helper.description,
                "required_fields": required_fields,
                "optional_fields": optional_fields,
                "nested_fields": nested_fields,
                "values": values,
            },
        )

    @app.post("/projects/{project_id}/entities", response_class=HTMLResponse)
    async def project_entity_create(
        request: Request,
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Create or update an entity."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        form_data = await request.form()
        entity_type = str(form_data.get("_entity_type", ""))
        node_id = str(form_data.get("_node_id", "")) or None

        if not entity_type:
            return HTMLResponse("<div class='error'>Missing entity type</div>")

        # Load project to get profile/version
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project_id)
        state.profile = project.profile
        state.version = project.version
        facade = state.get_or_create_facade()

        try:
            helper = getattr(facade, entity_type)
        except AttributeError:
            return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

        # Collect form values, converting types as needed
        values: dict[str, str | int | float | bool] = {}
        for field_name in helper.all_fields:
            raw_value = form_data.get(field_name)
            if raw_value is None or raw_value == "":
                continue

            raw_str = str(raw_value)
            info = helper.field_info(field_name)
            field_type = info.get("type", "string")

            # Type conversion
            if field_type == "integer":
                values[field_name] = int(raw_str)
            elif field_type == "float":
                values[field_name] = float(raw_str)
            elif field_type == "boolean":
                values[field_name] = raw_str.lower() == "true"
            else:
                values[field_name] = raw_str

        # Create or update instance
        try:
            instance = helper.create(**values)

            if node_id and node_id in state.nodes_by_id:
                # Update existing node
                state.update_node(node_id, instance)
                success_msg = f"{entity_type} updated successfully."
            else:
                # Create new node
                node = state.add_node(entity_type, instance)
                node_id = node.id
                success_msg = f"{entity_type} created successfully."

            # Re-render form with success message
            fields = []
            for field_name in helper.all_fields:
                info = helper.field_info(field_name)
                fields.append(
                    {
                        "name": field_name,
                        "type": info.get("type", "string"),
                        "required": info.get("required", False),
                        "description": info.get("description", ""),
                        "constraints": info.get("constraints"),
                        "item_type": info.get("items"),
                        "is_nested": info.get("type") in ("list", "entity")
                        and info.get("items") is not None,
                    }
                )

            required_fields = [f for f in fields if f["required"] and not f["is_nested"]]
            optional_fields = [f for f in fields if not f["required"] and not f["is_nested"]]
            nested_fields = [f for f in fields if f["is_nested"]]

            response = templates.TemplateResponse(
                request=request,
                name="partials/entity_form.html",
                context={
                    "request": request,
                    "project_id": project_id,
                    "entity_type": entity_type,
                    "node_id": node_id,
                    "description": helper.description,
                    "required_fields": required_fields,
                    "optional_fields": optional_fields,
                    "nested_fields": nested_fields,
                    "values": instance.model_dump(exclude_none=True),
                    "success": success_msg,
                },
            )
            # Trigger tree refresh via HTMX
            response.headers["HX-Trigger"] = "entityChanged"
            return response

        except Exception as e:
            # Re-render form with error
            fields = []
            for field_name in helper.all_fields:
                info = helper.field_info(field_name)
                fields.append(
                    {
                        "name": field_name,
                        "type": info.get("type", "string"),
                        "required": info.get("required", False),
                        "description": info.get("description", ""),
                        "constraints": info.get("constraints"),
                        "item_type": info.get("items"),
                        "is_nested": info.get("type") in ("list", "entity")
                        and info.get("items") is not None,
                    }
                )

            required_fields = [f for f in fields if f["required"] and not f["is_nested"]]
            optional_fields = [f for f in fields if not f["required"] and not f["is_nested"]]
            nested_fields = [f for f in fields if f["is_nested"]]

            return templates.TemplateResponse(
                request=request,
                name="partials/entity_form.html",
                context={
                    "request": request,
                    "project_id": project_id,
                    "entity_type": entity_type,
                    "node_id": node_id,
                    "description": helper.description,
                    "required_fields": required_fields,
                    "optional_fields": optional_fields,
                    "nested_fields": nested_fields,
                    "values": values,
                    "error": str(e),
                },
            )

    @app.get("/projects/{project_id}/entity/{node_id}", response_class=HTMLResponse)
    async def project_entity_edit(
        request: Request,
        project_id: str,
        node_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Return form for editing an existing entity."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        # Load project to get profile/version
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project_id)
        state.profile = project.profile
        state.version = project.version

        if node_id not in state.nodes_by_id:
            return HTMLResponse("<div class='error'>Entity not found</div>")

        node = state.nodes_by_id[node_id]
        entity_type = node.entity_type
        facade = state.get_or_create_facade()

        try:
            helper = getattr(facade, entity_type)
        except AttributeError:
            return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

        # Build field data
        fields = []
        for field_name in helper.all_fields:
            info = helper.field_info(field_name)
            fields.append(
                {
                    "name": field_name,
                    "type": info.get("type", "string"),
                    "required": info.get("required", False),
                    "description": info.get("description", ""),
                    "constraints": info.get("constraints"),
                    "item_type": info.get("items"),
                    "is_nested": info.get("type") in ("list", "entity")
                    and info.get("items") is not None,
                }
            )

        required_fields = [f for f in fields if f["required"] and not f["is_nested"]]
        optional_fields = [f for f in fields if not f["required"] and not f["is_nested"]]
        nested_fields = [f for f in fields if f["is_nested"]]

        # Get current values from the node
        values: dict[str, object] = {}
        if hasattr(node.instance, "model_dump"):
            values = node.instance.model_dump(exclude_none=True)

        return templates.TemplateResponse(
            request=request,
            name="partials/entity_form.html",
            context={
                "request": request,
                "project_id": project_id,
                "entity_type": entity_type,
                "node_id": node_id,
                "description": helper.description,
                "required_fields": required_fields,
                "optional_fields": optional_fields,
                "nested_fields": nested_fields,
                "values": values,
            },
        )

    @app.delete("/projects/{project_id}/entity/{node_id}", response_class=HTMLResponse)
    async def project_entity_delete(
        request: Request,
        project_id: str,
        node_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Delete an entity."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        state = get_project_state(project_id)

        if node_id not in state.nodes_by_id:
            return HTMLResponse("<div class='error'>Entity not found</div>")

        # Delete the node
        state.delete_node(node_id)

        # Return updated tree
        tree_data = state.get_tree_data()

        if not tree_data:
            return HTMLResponse("<div class='empty-state'><p>No entities yet.</p></div>")

        html = "<ul class='entity-tree'>"
        for item in tree_data:
            item_id = item.get("id", "")
            item_name = item.get("name", "Unnamed")
            item_type = item.get("type", "Entity")
            html += f"""<li class='entity-item'>
                <span class='entity-type-badge'>{item_type}</span>
                <a href='#' class='entity-name'
                   hx-get='/hub/projects/{project_id}/entity/{item_id}'
                   hx-target='#editor'>{item_name}</a>
                <button class='entity-delete' title='Delete'
                        hx-delete='/hub/projects/{project_id}/entity/{item_id}'
                        hx-target='#entity-tree'
                        hx-confirm='Delete this {item_type}?'>x</button>
            </li>"""
        html += "</ul>"
        return HTMLResponse(html)

    @app.post("/projects/{project_id}/chat", response_class=HTMLResponse)
    async def project_chat(
        request: Request,
        project_id: str,
        message: Annotated[str, Form()],
    ) -> HTMLResponse:
        """Post a chat message."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        # For now, just echo the message back
        html = f"<div class='chat-message'><strong>{user.name}:</strong> {message}</div>"
        return HTMLResponse(html)

    async def get_or_create_tenant(session: AsyncSession, user: TokenUser) -> Tenant:
        """Get or create tenant for user based on keycloak_id."""
        # Use keycloak_id as tenant slug for single-user tenants
        slug = user.keycloak_id[:8]
        result = await session.execute(select(Tenant).where(Tenant.slug == slug))
        tenant = result.scalar_one_or_none()
        if not tenant:
            tenant = Tenant(name=user.name or user.email, slug=slug)
            session.add(tenant)
            await session.commit()
            await session.refresh(tenant)
        return tenant

    # Mount metaseed UI for project-specific editing
    # Each project gets its own state via the project_id parameter
    @app.get("/projects/{project_id}/edit", response_class=HTMLResponse)
    async def project_metaseed_ui(
        request: Request,
        project_id: str,
    ) -> Response:
        """Embedded metaseed UI for a project."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse("/hub/")

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
