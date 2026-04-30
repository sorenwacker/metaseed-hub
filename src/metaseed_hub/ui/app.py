"""Hub UI application that extends metaseed's HTMX interface.

Adds authentication, project management, and collaboration features
on top of the base metaseed entity editing UI.
"""

import re
import secrets
import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from metaseed.specs.loader import SpecLoader
from metaseed.ui.state import AppState, TreeNode
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
CSRF_TOKEN_COOKIE = "metaseed_csrf_token"

# OIDC discovery cache
_oidc_config: dict[str, str] | None = None


async def get_oidc_config() -> dict[str, str]:
    """Fetch and cache OIDC discovery configuration."""
    global _oidc_config
    if _oidc_config is not None:
        return _oidc_config

    settings = get_settings()
    discovery_url = settings.oidc_discovery_url

    async with httpx.AsyncClient() as client:
        response = await client.get(discovery_url)
        response.raise_for_status()
        _oidc_config = response.json()
        return _oidc_config


def get_or_create_csrf_token(request: Request) -> str:
    """Get existing CSRF token from cookie or create a new one."""
    token = request.cookies.get(CSRF_TOKEN_COOKIE)
    if token and len(token) == 43:  # Base64 encoded 32 bytes
        return token
    return secrets.token_urlsafe(32)


def validate_csrf_token(request: Request) -> bool:
    """Validate CSRF token from header matches cookie.

    Returns True if valid, False otherwise.
    """
    cookie_token = request.cookies.get(CSRF_TOKEN_COOKIE)
    header_token = request.headers.get("X-CSRF-Token")

    if not cookie_token or not header_token:
        return False

    # Constant-time comparison to prevent timing attacks
    return secrets.compare_digest(cookie_token, header_token)


async def get_current_user_from_cookie(request: Request) -> TokenUser | None:
    """Extract and verify user from access token cookie."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        return await verify_token(token)
    except Exception:
        return None


def serialize_tree(state: AppState) -> dict[str, Any]:
    """Serialize AppState entity tree to JSON-compatible dict.

    Args:
        state: AppState with entity tree to serialize.

    Returns:
        Dictionary that can be stored as JSONB in database.
    """

    def serialize_node(node: TreeNode) -> dict[str, Any]:
        node_data: dict[str, Any] = {
            "id": node.id,
            "entity_type": node.entity_type,
            "label": node.label,
            "parent_id": node.parent_id,
            "data": {},
        }
        if node.instance and hasattr(node.instance, "model_dump"):
            # Use mode="json" to ensure datetime objects are serialized to strings
            node_data["data"] = node.instance.model_dump(exclude_none=True, mode="json")
        if node.children:
            node_data["children"] = [serialize_node(c) for c in node.children]
        return node_data

    return {
        "profile": state.profile,
        "version": state.version,
        "tree": [serialize_node(n) for n in state.entity_tree],
    }


def deserialize_tree(state: AppState, data: dict[str, Any]) -> None:
    """Deserialize JSON data into AppState entity tree.

    Args:
        state: AppState to populate.
        data: Dictionary loaded from database JSONB.
    """
    if not data or "tree" not in data:
        return

    facade = state.get_or_create_facade()

    def deserialize_node(
        node_data: dict[str, Any],
        parent_id: str | None = None,
    ) -> TreeNode | None:
        entity_type = node_data.get("entity_type")
        if not entity_type:
            return None

        try:
            helper = getattr(facade, entity_type)
        except AttributeError:
            return None

        # Create instance from stored data
        instance_data = node_data.get("data", {})
        try:
            instance = helper.create(**instance_data)
        except Exception:
            return None

        node = TreeNode(
            id=node_data.get("id", ""),
            entity_type=entity_type,
            instance=instance,
            label=node_data.get("label", f"New {entity_type}"),
            parent_id=parent_id,
        )

        # Recursively deserialize children
        for child_data in node_data.get("children", []):
            child = deserialize_node(child_data, parent_id=node.id)
            if child:
                node.children.append(child)
                state.nodes_by_id[child.id] = child

        return node

    state.entity_tree = []
    state.nodes_by_id = {}

    for node_data in data.get("tree", []):
        node = deserialize_node(node_data)
        if node:
            state.entity_tree.append(node)
            state.nodes_by_id[node.id] = node


def build_inline_tables(
    state: AppState,
    node_id: str,
    nested_fields: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build inline table data for nested fields of a node.

    Args:
        state: Application state containing nodes.
        node_id: ID of the parent node.
        nested_fields: List of nested field definitions.

    Returns:
        Dictionary mapping field names to table data.
    """
    if node_id not in state.nodes_by_id:
        return {}

    node = state.nodes_by_id[node_id]
    facade = state.get_or_create_facade()
    inline_tables: dict[str, dict[str, Any]] = {}

    # Primitive types that are not entities
    primitive_types = {"string", "integer", "float", "boolean", "date", "datetime", "uri"}

    for field in nested_fields:
        field_name = field["name"]
        item_type = field.get("item_type")
        if not item_type:
            inline_tables[field_name] = {
                "columns": [],
                "rows": [],
                "column_types": {},
                "nested_entity_type": "Unknown",
            }
            continue

        # Handle primitive list types (e.g., list of strings)
        if item_type.lower() in primitive_types:
            # Load existing values from parent instance
            rows = []
            if hasattr(node.instance, "model_dump"):
                parent_data = node.instance.model_dump(exclude_none=True)
                current_list = parent_data.get(field_name, []) or []
                for idx, val in enumerate(current_list):
                    rows.append({"_idx": idx, "value": val})

            inline_tables[field_name] = {
                "columns": ["value"],
                "rows": rows,
                "column_types": {"value": item_type.lower()},
                "nested_entity_type": item_type,
                "is_primitive_list": True,
            }
            continue

        # Get helper for the nested entity type
        try:
            helper = getattr(facade, item_type)
        except AttributeError:
            # Add empty table with error info
            inline_tables[field_name] = {
                "columns": [],
                "rows": [],
                "column_types": {},
                "nested_entity_type": item_type,
                "error": f"Unknown entity type: {item_type}",
            }
            continue

        # Find children of this type under this node
        children = [child for child in node.children if child.entity_type == item_type]

        # Build columns from helper's simple fields (exclude nested)
        columns = []
        column_types = {}
        required_columns = set()
        for fname in helper.all_fields:
            info = helper.field_info(fname)
            is_nested = info.get("type") in ("list", "entity") and info.get("items")
            if not is_nested:
                columns.append(fname)
                column_types[fname] = info.get("type", "string")
                if info.get("required"):
                    required_columns.add(fname)

        # Show all non-nested columns
        display_columns = columns

        # Build rows from children
        rows = []
        for idx, child in enumerate(children):
            row_data = {"_idx": idx, "_node_id": child.id}
            if hasattr(child.instance, "model_dump"):
                data = child.instance.model_dump(exclude_none=True)
                for col in display_columns:
                    val = data.get(col, "")
                    # Truncate long values for display
                    if isinstance(val, str) and len(val) > 50:
                        val = val[:47] + "..."
                    row_data[col] = val
            rows.append(row_data)

        # Determine which columns are inherited (reference to parent)
        parent_type_lower = node.entity_type.lower()
        inherited_columns = set()
        for col in display_columns:
            if col.endswith("_id"):
                ref_type = col[:-3]  # Remove "_id" suffix
                if ref_type == parent_type_lower:
                    inherited_columns.add(col)

        inline_tables[field_name] = {
            "columns": display_columns,
            "rows": rows,
            "column_types": column_types,
            "nested_entity_type": item_type,
            "inherited_columns": list(inherited_columns),
            "required_columns": list(required_columns),
        }

    return inline_tables


def create_hub_app() -> FastAPI:
    """Create the Hub FastAPI application with extended UI.

    Returns:
        FastAPI application with hub routes and mounted metaseed UI.
    """
    app = FastAPI(title="Metaseed Hub")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def render_template(
        request: Request,
        name: str,
        context: dict[str, Any],
        status_code: int = 200,
    ) -> Response:
        """Render template with CSRF token included.

        Automatically adds CSRF token to context and sets cookie.
        """
        csrf_token = get_or_create_csrf_token(request)
        context["csrf_token"] = csrf_token
        context["request"] = request

        response = templates.TemplateResponse(
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

    def escape_pattern_hyphen(pattern: str) -> str:
        """Escape hyphens in regex character classes for HTML pattern attribute.

        Modern browsers use RegExp 'v' flag which requires escaping hyphens
        that are not part of a valid range (like a-z or 0-9).
        Common problematic pattern: [A-Za-z0-9_-] where _- is not a valid range.
        """
        if not pattern:
            return pattern
        # Escape hyphens that follow underscore or other non-range chars
        # Pattern: _-] or _-x where x is not forming a valid range
        result = re.sub(r"(_)-(\])", r"\1\\-\2", pattern)
        result = re.sub(r"(_)-([^\]])", r"\1\\-\2", result)
        return result

    templates.env.filters["escape_pattern"] = escape_pattern_hyphen

    # Mount hub static files
    app.mount("/hub-static", StaticFiles(directory=str(STATIC_DIR)), name="hub-static")

    # Add spec builder routes
    spec_builder_router = create_spec_builder_router(templates)
    app.include_router(spec_builder_router)

    # Store for project-specific metaseed app states
    project_states: dict[str, AppState] = {}

    def get_project_state(project: Project) -> AppState:
        """Get or create AppState for a project, loading from database if needed.

        Args:
            project: Project model with profile, version, and data fields.

        Returns:
            AppState populated with project's entity tree.
        """
        project_id = project.id
        if project_id not in project_states:
            state = AppState()
            state.profile = project.profile
            state.version = project.version
            # Load existing data from database
            if project.data:
                deserialize_tree(state, project.data)
            project_states[project_id] = state
        else:
            state = project_states[project_id]
            state.profile = project.profile
            state.version = project.version
        return state

    async def save_project_state(
        session: AsyncSession,
        project: Project,
        state: AppState,
    ) -> None:
        """Save AppState entity tree to database.

        Args:
            session: Database session.
            project: Project model to update.
            state: AppState with entity tree to save.
        """
        from sqlalchemy.orm.attributes import flag_modified

        project.data = serialize_tree(state)
        # Explicitly mark JSONB field as modified for SQLAlchemy change detection
        flag_modified(project, "data")
        session.add(project)  # Ensure project is in session
        await session.commit()
        await session.refresh(project)

    settings = get_settings()
    hub_base_url = f"{settings.app_url}/hub"

    @app.get("/auth/login")
    async def auth_login(request: Request) -> RedirectResponse:
        """Redirect to OIDC provider login page."""
        oidc_config = await get_oidc_config()
        state = secrets.token_urlsafe(32)
        redirect_uri = f"{hub_base_url}/auth/callback"

        params = {
            "client_id": settings.effective_client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
        }

        auth_url = f"{oidc_config['authorization_endpoint']}?{urlencode(params)}"

        response = RedirectResponse(url=auth_url, status_code=302)
        response.set_cookie(
            key=STATE_COOKIE,
            value=state,
            httponly=True,
            secure=True,
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
        """Handle OAuth callback from OIDC provider."""
        if error:
            return RedirectResponse(url="/hub/?error=auth_failed", status_code=302)

        stored_state = request.cookies.get(STATE_COOKIE)
        if not state or state != stored_state:
            return RedirectResponse(url="/hub/?error=invalid_state", status_code=302)

        if not code:
            return RedirectResponse(url="/hub/?error=no_code", status_code=302)

        # Exchange code for tokens
        oidc_config = await get_oidc_config()
        redirect_uri = f"{hub_base_url}/auth/callback"

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                oidc_config["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.effective_client_id,
                    "client_secret": settings.effective_client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )

        if token_response.status_code != 200:
            import logging

            logging.error(
                f"Token exchange failed: {token_response.status_code} - {token_response.text}"
            )
            return RedirectResponse(url="/hub/?error=token_exchange_failed", status_code=302)

        tokens = token_response.json()
        access_token = tokens.get("access_token")

        response = RedirectResponse(url="/hub/", status_code=302)
        response.delete_cookie(key=STATE_COOKIE)
        response.set_cookie(
            key=ACCESS_TOKEN_COOKIE,
            value=access_token,
            httponly=True,
            secure=True,
            max_age=tokens.get("expires_in", 3600),
            path="/",
        )
        return response

    @app.get("/auth/logout")
    async def auth_logout(request: Request) -> RedirectResponse:
        """Logout and redirect to OIDC provider logout."""
        oidc_config = await get_oidc_config()
        logout_url = oidc_config.get("end_session_endpoint")

        response = RedirectResponse(url="/hub/", status_code=302)
        response.delete_cookie(key=ACCESS_TOKEN_COOKIE)

        if logout_url:
            params = {
                "client_id": settings.effective_client_id,
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
    ) -> Response:
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

        return render_template(
            request=request,
            name="home.html",
            context={
                "user": user,
                "workspaces": workspaces,
            },
        )

    @app.get("/workspaces/new", response_class=HTMLResponse)
    async def workspace_new(request: Request) -> Response:
        """Return workspace creation form."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        return render_template(
            request=request,
            name="partials/workspace_form.html",
            context={"user": user},
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

        if not validate_csrf_token(request):
            return RedirectResponse("/hub/?error=csrf_validation_failed", status_code=302)

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

        return render_template(
            request=request,
            name="workspace.html",
            context={
                "user": user,
                "workspace": workspace,
                "projects": projects,
            },
        )

    @app.get("/projects/new", response_class=HTMLResponse)
    async def project_new(
        request: Request,
        workspace_id: str,
    ) -> Response:
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

        return render_template(
            request=request,
            name="partials/project_form.html",
            context={
                "user": user,
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

        if not validate_csrf_token(request):
            url = f"/hub/workspaces/{workspace_id}?error=csrf_validation_failed"
            return RedirectResponse(url, status_code=302)

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

        if not validate_csrf_token(request):
            return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

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

        # Get or create state for this project (loads from database)
        state = get_project_state(project)

        return render_template(
            request=request,
            name="project.html",
            context={
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

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project)
        tree_data = state.get_tree_data()

        if not tree_data:
            return HTMLResponse("<div class='empty-state'><p>No entities yet.</p></div>")

        # Render tree with entity types and actions
        html = "<ul class='entity-tree'>"
        for item in tree_data:
            item_id = item.get("id", "")
            item_name = item.get("label") or item.get("name") or "Unnamed"
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
        parent_id: str | None = None,
        parent_field: str | None = None,
    ) -> Response:
        """Return form for creating or editing an entity."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        # Load project to get profile/version
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project)
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
        inline_tables: dict[str, dict[str, Any]] = {}
        if node_id and node_id in state.nodes_by_id:
            node = state.nodes_by_id[node_id]
            if hasattr(node.instance, "model_dump"):
                values = node.instance.model_dump(exclude_none=True)
            inline_tables = build_inline_tables(state, node_id, nested_fields)

        return render_template(
            request=request,
            name="partials/entity_form.html",
            context={
                "project_id": project_id,
                "entity_type": entity_type,
                "node_id": node_id,
                "parent_id": parent_id,
                "parent_field": parent_field,
                "description": helper.description,
                "required_fields": required_fields,
                "optional_fields": optional_fields,
                "nested_fields": nested_fields,
                "inline_tables": inline_tables,
                "values": values,
            },
        )

    @app.post("/projects/{project_id}/entities", response_class=HTMLResponse)
    async def project_entity_create(
        request: Request,
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Create or update an entity."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        if not validate_csrf_token(request):
            return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

        form_data = await request.form()
        entity_type = str(form_data.get("_entity_type", ""))
        node_id = str(form_data.get("_node_id", "")) or None
        parent_id = str(form_data.get("_parent_id", "")) or None
        # parent_field not used by add_node but kept for template

        if not entity_type:
            return HTMLResponse("<div class='error'>Missing entity type</div>")

        # Load project to get profile/version
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project)
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
                node = state.nodes_by_id[node_id]
                node.instance = instance
                # Update label if there's a name/title field
                for label_field in ("title", "name", "unique_id", "id"):
                    if hasattr(instance, label_field):
                        label_val = getattr(instance, label_field)
                        if label_val:
                            node.label = str(label_val)
                            break
                success_msg = f"{entity_type} updated successfully."
            else:
                # Create new node
                node = state.add_node(entity_type, instance, parent_id=parent_id)
                node_id = node.id
                success_msg = f"{entity_type} created successfully."

            # Save to database
            await save_project_state(session, project, state)

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

            # Build inline tables for the saved entity
            inline_tables = build_inline_tables(state, node_id, nested_fields)

            response = render_template(
                request=request,
                name="partials/entity_form.html",
                context={
                    "project_id": project_id,
                    "entity_type": entity_type,
                    "node_id": node_id,
                    "parent_id": parent_id,
                    "description": helper.description,
                    "required_fields": required_fields,
                    "optional_fields": optional_fields,
                    "nested_fields": nested_fields,
                    "inline_tables": inline_tables,
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

            # Build inline tables if editing existing entity
            inline_tables = {}
            if node_id:
                inline_tables = build_inline_tables(state, node_id, nested_fields)

            return render_template(
                request=request,
                name="partials/entity_form.html",
                context={
                    "project_id": project_id,
                    "entity_type": entity_type,
                    "node_id": node_id,
                    "description": helper.description,
                    "required_fields": required_fields,
                    "optional_fields": optional_fields,
                    "nested_fields": nested_fields,
                    "inline_tables": inline_tables,
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
    ) -> Response:
        """Return form for editing an existing entity."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        # Load project to get profile/version
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project)

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

        # Build inline tables for nested fields
        inline_tables = build_inline_tables(state, node_id, nested_fields)

        return render_template(
            request=request,
            name="partials/entity_form.html",
            context={
                "project_id": project_id,
                "entity_type": entity_type,
                "node_id": node_id,
                "parent_id": node.parent_id,
                "description": helper.description,
                "required_fields": required_fields,
                "optional_fields": optional_fields,
                "nested_fields": nested_fields,
                "inline_tables": inline_tables,
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

        if not validate_csrf_token(request):
            return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

        # Load project
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project)

        if node_id not in state.nodes_by_id:
            return HTMLResponse("<div class='error'>Entity not found</div>")

        # Delete the node
        state.delete_node(node_id)

        # Save to database
        await save_project_state(session, project, state)

        # Return updated tree
        tree_data = state.get_tree_data()

        if not tree_data:
            return HTMLResponse("<div class='empty-state'><p>No entities yet.</p></div>")

        html = "<ul class='entity-tree'>"
        for item in tree_data:
            item_id = item.get("id", "")
            item_name = item.get("label") or item.get("name") or "Unnamed"
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

    @app.post(
        "/projects/{project_id}/table/{parent_node_id}/{field_name}/row",
        response_class=HTMLResponse,
    )
    async def add_table_row(
        request: Request,
        project_id: str,
        parent_node_id: str,
        field_name: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Add a new row to an inline table."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        if not validate_csrf_token(request):
            return HTMLResponse("<tr><td>CSRF validation failed</td></tr>", status_code=403)

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<tr><td>Project not found</td></tr>")

        state = get_project_state(project)

        if parent_node_id not in state.nodes_by_id:
            return HTMLResponse("<tr><td>Parent entity not found</td></tr>")

        parent_node = state.nodes_by_id[parent_node_id]
        facade = state.get_or_create_facade()

        # Get the nested entity type from the parent's field info
        primitive_types = {"string", "integer", "float", "boolean", "date", "datetime", "uri"}
        try:
            parent_helper = getattr(facade, parent_node.entity_type)
            field_info = parent_helper.field_info(field_name)
            nested_type = field_info.get("items")
            if not nested_type:
                return HTMLResponse("<tr><td>Invalid field</td></tr>")

            # Handle primitive list types (list of strings, integers, etc.)
            if nested_type.lower() in primitive_types:
                # Get current list from parent instance
                current_list: list[Any] = []
                if hasattr(parent_node.instance, "model_dump"):
                    parent_data = parent_node.instance.model_dump(exclude_none=True)
                    current_list = parent_data.get(field_name, []) or []

                # Add new empty value
                row_idx = len(current_list)
                current_list.append("")

                # Update parent instance with new list
                update_data = parent_node.instance.model_dump(exclude_none=True)
                update_data[field_name] = current_list
                parent_helper = getattr(facade, parent_node.entity_type)
                updated_instance = parent_helper.create(**update_data)
                state.update_node(parent_node_id, updated_instance)

                # Save to database
                await save_project_state(session, project, state)

                # Return row HTML for primitive value
                input_type = "text"
                if nested_type.lower() in ("integer", "float"):
                    input_type = "number"
                elif nested_type.lower() == "date":
                    input_type = "date"
                elif nested_type.lower() == "datetime":
                    input_type = "datetime-local"

                post_url = f"/hub/projects/{project_id}/table/{parent_node_id}"
                post_url += f"/primitive/{field_name}/{row_idx}"
                html = f'<tr id="row-{field_name}-{row_idx}" data-idx="{row_idx}">'
                html += f"""<td class="editable-cell" data-col="value">
                    <span class="cell-display placeholder">Click to edit</span>
                    <input type="{input_type}" class="cell-input" name="value" value=""
                           hx-post="{post_url}"
                           hx-trigger="change, blur" hx-swap="none">
                </td>"""
                html += f"""<td class="row-actions">
                    <button type="button" class="btn-icon danger"
                            hx-delete="{post_url}"
                            hx-target="#row-{field_name}-{row_idx}"
                            hx-swap="delete"
                            hx-confirm="Delete this item?"
                            title="Delete">&#128465;</button>
                </td></tr>"""

                response = HTMLResponse(html)
                response.headers["HX-Trigger"] = "entityChanged"
                return response

            nested_helper = getattr(facade, nested_type)
        except AttributeError:
            return HTMLResponse("<tr><td>Unknown entity type</td></tr>")

        # Get parent's identifier for reference fields
        parent_identifier = None
        if hasattr(parent_node.instance, "model_dump"):
            parent_data = parent_node.instance.model_dump(exclude_none=True)
            # Try common identifier field names
            for id_field in ("unique_id", "id", "identifier", "name"):
                if id_field in parent_data:
                    parent_identifier = parent_data[id_field]
                    break

        # Generate default values for required fields
        default_values: dict[str, str | int | float | bool] = {}
        parent_type_lower = parent_node.entity_type.lower()
        for fname in nested_helper.all_fields:
            info = nested_helper.field_info(fname)
            if not info.get("required"):
                continue
            # Skip nested fields
            if info.get("type") in ("list", "entity") and info.get("items"):
                continue
            # Generate appropriate defaults based on type
            field_type = info.get("type", "string")
            if fname in ("unique_id", "id", "identifier"):
                default_values[fname] = str(uuid.uuid4())[:8]
            elif fname.endswith("_id"):
                # Check if this references the parent type
                ref_type = fname[:-3]  # Remove "_id" suffix
                if ref_type == parent_type_lower and parent_identifier:
                    default_values[fname] = str(parent_identifier)
                else:
                    default_values[fname] = str(uuid.uuid4())[:8]
            elif field_type == "integer":
                default_values[fname] = 0
            elif field_type == "float":
                default_values[fname] = 0.0
            elif field_type == "boolean":
                default_values[fname] = False
            else:
                # String or other - use field name as placeholder
                default_values[fname] = f"New {fname.replace('_', ' ').title()}"

        # Create instance with defaults
        instance = nested_helper.create(**default_values)

        # Add as child of parent
        child_node = state.add_node(nested_type, instance, parent_id=parent_node_id)

        # Save to database
        await save_project_state(session, project, state)

        # Get columns for display
        columns = []
        column_types = {}
        for fname in nested_helper.all_fields:
            info = nested_helper.field_info(fname)
            is_nested = info.get("type") in ("list", "entity") and info.get("items")
            if not is_nested:
                columns.append(fname)
                column_types[fname] = info.get("type", "string")

        # Build row HTML with actual values
        children_of_type = [c for c in parent_node.children if c.entity_type == nested_type]
        row_idx = len(children_of_type) - 1
        node_id = child_node.id
        # Get instance data for cell values
        instance_data = {}
        if hasattr(instance, "model_dump"):
            instance_data = instance.model_dump(exclude_none=True)

        # Determine inherited columns (reference to parent)
        inherited_cols = set()
        for col in columns:
            if col.endswith("_id"):
                ref_type = col[:-3]
                if ref_type == parent_type_lower:
                    inherited_cols.add(col)

        html = f'<tr id="row-{field_name}-{row_idx}" data-idx="{row_idx}" '
        html += f'data-node-id="{node_id}">'
        for col in columns:
            col_type = column_types.get(col, "string")
            cell_value = instance_data.get(col, "")

            # Inherited columns are read-only
            if col in inherited_cols:
                html += f"""<td class="readonly-cell" data-col="{col}">
                    <span class="cell-display inherited">{cell_value}</span>
                </td>"""
            else:
                if col_type in ("integer", "float"):
                    input_type = "number"
                elif col_type == "date":
                    input_type = "date"
                elif col_type == "datetime":
                    input_type = "datetime-local"
                else:
                    input_type = "text"
                step = 'step="any"' if col_type == "float" else ""
                display_value = cell_value or "Click to edit"
                placeholder_class = " placeholder" if not cell_value else ""
                post_url = f"/hub/projects/{project_id}/table/{child_node.id}/cell"
                html += f"""<td class="editable-cell" data-col="{col}">
                    <span class="cell-display{placeholder_class}">{display_value}</span>
                    <input type="{input_type}" class="cell-input" name="{col}"
                           value="{cell_value}" {step}
                           hx-post="{post_url}" hx-trigger="change, blur" hx-swap="none">
                </td>"""
        html += f"""<td class="row-actions">
            <button type="button" class="btn-icon"
                    hx-get="/hub/projects/{project_id}/entity/{child_node.id}"
                    hx-target="#editor"
                    hx-swap="innerHTML"
                    title="Edit">&#9998;</button>
            <button type="button" class="btn-icon danger"
                    hx-delete="/hub/projects/{project_id}/entity/{child_node.id}"
                    hx-target="#row-{field_name}-{row_idx}"
                    hx-swap="delete"
                    hx-confirm="Delete this {nested_type}?"
                    title="Delete">&#128465;</button>
        </td></tr>"""

        response = HTMLResponse(html)
        response.headers["HX-Trigger"] = "entityChanged"
        return response

    @app.post("/projects/{project_id}/table/{node_id}/cell")
    async def update_table_cell(
        request: Request,
        project_id: str,
        node_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Update a single cell value in an inline table."""
        print(f"DEBUG update_table_cell: project_id={project_id}, node_id={node_id}")

        user = await get_current_user_from_cookie(request)
        if not user:
            print("DEBUG update_table_cell: No user - 401")
            return HTMLResponse(status_code=401)

        if not validate_csrf_token(request):
            cookie = request.cookies.get(CSRF_TOKEN_COOKIE)
            header = request.headers.get("X-CSRF-Token")
            print(f"DEBUG update_table_cell: CSRF failed - cookie={cookie}, header={header}")
            return HTMLResponse(status_code=403)

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            print("DEBUG update_table_cell: Project not found - 404")
            return HTMLResponse(status_code=404)

        state = get_project_state(project)
        print(f"DEBUG update_table_cell: nodes_by_id keys = {list(state.nodes_by_id.keys())}")

        if node_id not in state.nodes_by_id:
            print(f"DEBUG update_table_cell: Node {node_id} not found in state - 404")
            return HTMLResponse(status_code=404)

        node = state.nodes_by_id[node_id]
        facade = state.get_or_create_facade()

        try:
            helper = getattr(facade, node.entity_type)
        except AttributeError:
            print(f"DEBUG update_table_cell: Entity type {node.entity_type} not found - 400")
            return HTMLResponse(status_code=400)

        # Get form data (single field update)
        form_data = await request.form()
        print(f"DEBUG update_table_cell: form_data = {dict(form_data)}")

        # Get current values
        current_values = {}
        if hasattr(node.instance, "model_dump"):
            current_values = node.instance.model_dump(exclude_none=True)
        print(f"DEBUG update_table_cell: current_values = {current_values}")

        # Update with new values from form (skip internal fields like _csrf_token)
        updated_fields = []
        for field_name, raw_value in form_data.items():
            if field_name.startswith("_"):
                print(f"DEBUG update_table_cell: skipping internal field {field_name}")
                continue
            if raw_value is None or raw_value == "":
                print(f"DEBUG update_table_cell: skipping empty field {field_name}")
                continue

            raw_str = str(raw_value)
            info = helper.field_info(field_name)
            field_type = info.get("type", "string")

            if field_type == "integer":
                current_values[field_name] = int(raw_str)
            elif field_type == "float":
                current_values[field_name] = float(raw_str)
            elif field_type == "boolean":
                current_values[field_name] = raw_str.lower() == "true"
            else:
                current_values[field_name] = raw_str
            updated_fields.append(field_name)

        print(f"DEBUG update_table_cell: updated fields = {updated_fields}")
        print(f"DEBUG update_table_cell: final values = {current_values}")

        # Create updated instance
        instance = helper.create(**current_values)
        state.update_node(node_id, instance)

        # Save to database
        await save_project_state(session, project, state)
        print("DEBUG update_table_cell: saved successfully")

        # Return success with entityChanged trigger for graph updates
        return Response(
            status_code=200,
            headers={"HX-Trigger": "entityChanged"},
        )

    @app.post(
        "/projects/{project_id}/table/{node_id}/primitive/{field_name}/{idx}",
        response_class=HTMLResponse,
    )
    async def update_primitive_list_item(
        request: Request,
        project_id: str,
        node_id: str,
        field_name: str,
        idx: int,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Update a primitive list item value."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        if not validate_csrf_token(request):
            return HTMLResponse(status_code=403)

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse(status_code=404)

        state = get_project_state(project)

        if node_id not in state.nodes_by_id:
            return HTMLResponse(status_code=404)

        node = state.nodes_by_id[node_id]
        facade = state.get_or_create_facade()

        try:
            helper = getattr(facade, node.entity_type)
        except AttributeError:
            return HTMLResponse(status_code=400)

        # Get form data
        form_data = await request.form()
        new_value = str(form_data.get("value", ""))

        # Get current values and update the list
        current_values = {}
        if hasattr(node.instance, "model_dump"):
            current_values = node.instance.model_dump(exclude_none=True)

        current_list = current_values.get(field_name, []) or []
        if idx < len(current_list):
            current_list[idx] = new_value
        current_values[field_name] = current_list

        # Create updated instance
        instance = helper.create(**current_values)
        state.update_node(node_id, instance)

        # Save to database
        await save_project_state(session, project, state)

        return HTMLResponse(status_code=200)

    @app.delete(
        "/projects/{project_id}/table/{node_id}/primitive/{field_name}/{idx}",
        response_class=HTMLResponse,
    )
    async def delete_primitive_list_item(
        request: Request,
        project_id: str,
        node_id: str,
        field_name: str,
        idx: int,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Delete a primitive list item."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        if not validate_csrf_token(request):
            return HTMLResponse(status_code=403)

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse(status_code=404)

        state = get_project_state(project)

        if node_id not in state.nodes_by_id:
            return HTMLResponse(status_code=404)

        node = state.nodes_by_id[node_id]
        facade = state.get_or_create_facade()

        try:
            helper = getattr(facade, node.entity_type)
        except AttributeError:
            return HTMLResponse(status_code=400)

        # Get current values and remove from list
        current_values = {}
        if hasattr(node.instance, "model_dump"):
            current_values = node.instance.model_dump(exclude_none=True)

        current_list = current_values.get(field_name, []) or []
        if idx < len(current_list):
            current_list.pop(idx)
        current_values[field_name] = current_list

        # Create updated instance
        instance = helper.create(**current_values)
        state.update_node(node_id, instance)

        # Save to database
        await save_project_state(session, project, state)

        response = HTMLResponse(status_code=200)
        response.headers["HX-Trigger"] = "entityChanged"
        return response

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

        if not validate_csrf_token(request):
            return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

        # For now, just echo the message back
        html = f"<div class='chat-message'><strong>{user.name}:</strong> {message}</div>"
        return HTMLResponse(html)

    @app.post("/projects/{project_id}/validate", response_class=HTMLResponse)
    async def project_validate(
        request: Request,
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> HTMLResponse:
        """Validate all entities in the project against their schemas."""
        from pydantic import ValidationError

        user = await get_current_user_from_cookie(request)
        if not user:
            return HTMLResponse(status_code=401)

        if not validate_csrf_token(request):
            return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return HTMLResponse("<div class='error'>Project not found</div>")

        state = get_project_state(project)
        facade = state.get_or_create_facade()

        errors: list[dict[str, Any]] = []
        valid_count = 0

        for node_id, node in state.nodes_by_id.items():
            try:
                helper = getattr(facade, node.entity_type)
                data = node.instance.model_dump(exclude_none=True) if node.instance else {}
                # Re-validate by recreating - Pydantic validation runs here
                helper.create(**data)
                valid_count += 1
            except ValidationError as e:
                errors.append(
                    {
                        "node_id": node_id,
                        "entity_type": node.entity_type,
                        "label": node.label,
                        "errors": [
                            {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
                            for err in e.errors()
                        ],
                    }
                )
            except AttributeError:
                errors.append(
                    {
                        "node_id": node_id,
                        "entity_type": node.entity_type,
                        "label": node.label,
                        "errors": [
                            {"field": "", "message": f"Unknown entity type: {node.entity_type}"}
                        ],
                    }
                )
            except Exception as e:
                errors.append(
                    {
                        "node_id": node_id,
                        "entity_type": node.entity_type,
                        "label": node.label,
                        "errors": [{"field": "", "message": str(e)}],
                    }
                )

        # Build HTML response
        total = len(state.nodes_by_id)
        if not errors:
            html = f"""
            <div class="validation-results success">
                <div class="validation-header success-message">
                    All {total} entities are valid.
                </div>
            </div>
            """
        else:
            html = f"""
            <div class="validation-results">
                <div class="validation-header error-message">
                    {len(errors)} of {total} entities have validation errors.
                </div>
                <div class="validation-errors">
            """
            for err in errors:
                html += f"""
                <div class="validation-error-item">
                    <div class="validation-entity">
                        <span class="entity-type-badge">{err['entity_type']}</span>
                        <a href="#" class="entity-link"
                           hx-get="/hub/projects/{project_id}/entity/{err['node_id']}"
                           hx-target="#editor"
                           hx-swap="innerHTML">{err['label']}</a>
                    </div>
                    <ul class="validation-error-list">
                """
                for field_err in err["errors"]:
                    field = field_err["field"] or "(general)"
                    html += f"<li><strong>{field}:</strong> {field_err['message']}</li>"
                html += "</ul></div>"
            html += "</div></div>"

        return HTMLResponse(html)

    @app.get("/projects/{project_id}/graph", response_class=HTMLResponse)
    async def project_graph(
        request: Request,
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
        node_id: str | None = None,
    ) -> Response:
        """Graph visualization of project entities.

        Args:
            node_id: Optional. If provided, only show this entity and its descendants.
        """
        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse("/hub/")

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()

        if not project:
            return RedirectResponse("/hub/")

        return render_template(
            request=request,
            name="graph.html",
            context={
                "user": user,
                "project": project,
                "node_id": node_id,
            },
        )

    @app.get("/projects/{project_id}/api/graph")
    async def project_graph_api(
        project_id: str,
        session: Annotated[AsyncSession, Depends(get_session)],
        node_id: str | None = None,
    ) -> Response:
        """Return graph data for visualization (JSON API).

        Builds nodes and edges from the hub's tree structure for vis.js.

        Args:
            node_id: Optional. If provided, only include this node and its descendants.
        """
        from fastapi.responses import JSONResponse

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()

        if not project:
            return JSONResponse(content={"nodes": [], "edges": []})

        state = get_project_state(project)

        # Build graph from hub's tree structure (uses TreeNode.children)
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        node_ids: set[str] = set()

        def truncate(text: str, max_len: int = 25) -> str:
            if len(text) <= max_len:
                return text
            return text[: max_len - 1] + "..."

        def add_node(node: TreeNode, parent_vis_id: str | None = None) -> None:
            vis_id = node.id
            if vis_id in node_ids:
                return
            node_ids.add(vis_id)

            nodes.append(
                {
                    "id": vis_id,
                    "label": truncate(node.label, 25),
                    "title": f"{node.entity_type}: {node.label}",
                    "group": node.entity_type,
                }
            )

            if parent_vis_id:
                edges.append({"from": parent_vis_id, "to": vis_id})

            # Process children stored in TreeNode.children
            for child in node.children:
                add_node(child, vis_id)

        # If node_id specified, find that node and graph from there
        if node_id and node_id in state.nodes_by_id:
            start_node = state.nodes_by_id[node_id]
            add_node(start_node)
        else:
            # Graph all entities
            for root_node in state.entity_tree:
                add_node(root_node)

        return JSONResponse(content={"nodes": nodes, "edges": edges})

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
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Response:
        """Embedded metaseed UI for a project."""
        user = await get_current_user_from_cookie(request)
        if not user:
            return RedirectResponse("/hub/")

        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            return RedirectResponse("/hub/")

        state = get_project_state(project)

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
