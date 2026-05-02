"""Project routes for Hub UI."""

import copy
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from metaseed.ui.state import AppState, TreeNode
from sqlalchemy import select

from metaseed_hub.models import Project
from metaseed_hub.ui.dependencies import CurrentUser, DbSession
from metaseed_hub.ui.helpers import (
    CSRF_TOKEN_COOKIE,
    create_nested_nodes,
    get_or_create_csrf_token,
    get_project_state,
    serialize_tree,
    validate_csrf_token,
)

logger = logging.getLogger("metaseed_hub")

router = APIRouter(prefix="/projects", tags=["projects"])

# Templates reference, initialized by init_templates()
_templates: Jinja2Templates | None = None

# Module-level project states cache
project_states: dict[str, AppState] = {}


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


@router.get("/new", response_class=HTMLResponse)
async def project_new(
    request: Request,
    user: CurrentUser,
    workspace_id: str,
) -> Response:
    """Return project creation form."""
    from metaseed.specs.loader import SpecLoader

    # Get available profiles and versions from metaseed
    loader = SpecLoader()
    profiles_data = []
    for profile_name in loader.list_profiles():
        versions = loader.list_versions(profile_name)

        # Sort versions in descending order (newest first)
        def version_key(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)

        versions = sorted(versions, key=version_key, reverse=True)
        # Get profile metadata from latest version
        display_name = profile_name
        description = ""
        root_entity = "Investigation"
        if versions:
            try:
                spec = loader.load_profile(versions[0], profile_name)
                display_name = spec.display_name or profile_name
                description = spec.description or ""
                root_entity = spec.root_entity or "Investigation"
            except Exception:
                pass
        profiles_data.append(
            {
                "name": profile_name,
                "display_name": display_name,
                "description": description,
                "root_entity": root_entity,
                "versions": versions,
                "latest_version": versions[0] if versions else "",
            }
        )

    return _render_template(
        request=request,
        name="partials/project_form.html",
        context={
            "user": user,
            "workspace_id": workspace_id,
            "profiles": profiles_data,
        },
    )


@router.post("")
async def project_create(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    workspace_id: Annotated[str, Form()],
    name: Annotated[str, Form()],
    profile: Annotated[str, Form()],
    version: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
    load_example: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Create a new project."""
    import metaseed
    import yaml
    from metaseed.models import get_model
    from metaseed.specs.loader import SpecLoader

    if not validate_csrf_token(request, csrf_token):
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
    await session.refresh(project)

    # Load example data if requested
    logger.info(
        f"project_create: load_example={load_example!r}, profile={profile}, version={version}"
    )
    if load_example == "true":
        examples_dir = Path(metaseed.__file__).parent / "examples"
        version_dir = examples_dir / profile / version
        yaml_files = list(version_dir.glob("*.yaml")) if version_dir.exists() else []

        if yaml_files:
            try:
                example_data = yaml.safe_load(yaml_files[0].read_text(encoding="utf-8"))
                # Deep copy to prevent Pydantic from modifying the original dict
                example_data_copy = copy.deepcopy(example_data)

                loader = SpecLoader(profile=profile)
                spec = loader.load_profile(version, profile)
                root_entity = spec.root_entity or "Investigation"

                state = get_project_state(project, project_states)
                state.reset()
                state.profile = profile
                state.version = version
                state.facade = None
                facade = state.get_or_create_facade()

                Model = get_model(root_entity, version, profile=profile)
                instance = Model(**example_data)
                node = state.add_node(root_entity, instance)
                state.editing_node_id = node.id

                # Create nested child nodes from the unmodified copy
                create_nested_nodes(state, facade, node, root_entity, example_data_copy)

                from sqlalchemy.orm.attributes import flag_modified

                project.data = serialize_tree(state)
                flag_modified(project, "data")
                session.add(project)
                await session.commit()
            except Exception as e:
                logger.exception(f"Failed to load example data: {e}")

    return RedirectResponse(f"/hub/projects/{project.id}", status_code=303)


@router.delete("/{project_id}", response_class=HTMLResponse)
async def project_delete(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    """Delete a project."""
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


@router.post("/{project_id}/load-example", response_class=HTMLResponse)
async def project_load_example(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Load example data into a project from YAML files."""
    import metaseed
    import yaml
    from metaseed.models import get_model
    from metaseed.specs.loader import SpecLoader

    if not validate_csrf_token(request):
        return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse("<div class='error'>Project not found</div>")

    # Find example YAML file
    examples_dir = Path(metaseed.__file__).parent / "examples"
    version_dir = examples_dir / project.profile / project.version

    if not version_dir.exists():
        msg = f"No example available for {project.profile} v{project.version}"
        return HTMLResponse(f"<div class='error'>{msg}</div>")

    yaml_files = list(version_dir.glob("*.yaml"))
    if not yaml_files:
        msg = f"No example file found for {project.profile} v{project.version}"
        return HTMLResponse(f"<div class='error'>{msg}</div>")

    example_file = yaml_files[0]
    try:
        example_data = yaml.safe_load(example_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return HTMLResponse(f"<div class='error'>Error loading example: {e}</div>")

    # Deep copy to prevent Pydantic from modifying the original dict
    example_data_copy = copy.deepcopy(example_data)

    # Load spec to get root entity
    loader = SpecLoader(profile=project.profile)
    spec = loader.load_profile(project.version, project.profile)
    root_entity = spec.root_entity or "Investigation"

    # Load example into project state
    state = get_project_state(project, project_states)
    state.reset()
    state.profile = project.profile
    state.version = project.version
    state.facade = None
    facade = state.get_or_create_facade()

    try:
        Model = get_model(root_entity, project.version, profile=project.profile)
        instance = Model(**example_data)
        node = state.add_node(root_entity, instance)
        state.editing_node_id = node.id

        # Create nested child nodes from the unmodified copy
        create_nested_nodes(state, facade, node, root_entity, example_data_copy)

        # Save to database
        from sqlalchemy.orm.attributes import flag_modified

        project.data = serialize_tree(state)
        logger.info(f"Saving tree with {len(state.entity_tree)} root nodes")
        for n in state.entity_tree:
            if n.instance:
                data = n.instance.model_dump(exclude_none=True)
                logger.info(f"  {n.entity_type} '{n.label}': {len(data)} fields")
            for c in n.children[:3]:
                if c.instance:
                    cdata = c.instance.model_dump(exclude_none=True)
                    logger.info(f"    Child {c.entity_type} '{c.label}': {len(cdata)} fields")

        flag_modified(project, "data")
        session.add(project)
        await session.commit()

    except Exception as e:
        import traceback

        logger.exception(f"Failed to load example: {e}")
        tb = traceback.format_exc()
        error_html = f"""
        <div class='notification error' style='user-select: text;'>
            <strong>Error loading example:</strong>
            <pre style='white-space: pre-wrap; font-size: 0.75rem; margin-top: 0.5rem;'>{e}

{tb}</pre>
        </div>"""
        return HTMLResponse(error_html)

    # Use HX-Redirect for HTMX to do a full page redirect
    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/projects/{project_id}"
    return response


@router.get("/{project_id}", response_class=HTMLResponse)
async def project_editor(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Project editor - wraps metaseed UI with hub chrome."""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        return RedirectResponse("/hub/")

    # Get or create state for this project (loads from database)
    state = get_project_state(project, project_states)

    return _render_template(
        request=request,
        name="project.html",
        context={
            "user": user,
            "project": project,
            "state": state,
            "root_types": state.get_root_entity_types(),
        },
    )


@router.get("/{project_id}/tree", response_class=HTMLResponse)
async def project_tree(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    """Return entity tree for a project."""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse("<div class='error'>Project not found</div>")

    state = get_project_state(project, project_states)
    tree_data = state.get_tree_data()

    if not tree_data:
        return HTMLResponse("<div class='empty-state'><p>No entities yet.</p></div>")

    def render_tree_item(item: dict[str, Any], depth: int = 0) -> str:
        """Recursively render tree item and its children."""
        item_id = item.get("id", "")
        item_name = item.get("label") or item.get("name") or "Unnamed"
        item_type = item.get("entity_type") or item.get("type", "Entity")
        children = item.get("children", [])
        has_children = bool(children)
        is_nested = item.get("is_nested", False)
        child_count = len(children)

        # For nested items, link to form with parent context
        if is_nested:
            field_name = item.get("field_name", "")
            idx = item.get("idx", 0)
            base = f"/hub/projects/{project_id}"
            click_url = f"{base}/form/{item_type}?parent_field={field_name}&idx={idx}"
            delete_url = f"{base}/nested/{field_name}/{idx}"
        else:
            click_url = f"/hub/projects/{project_id}/entity/{item_id}"
            delete_url = f"/hub/projects/{project_id}/entity/{item_id}"

        # Expand button for items with children
        expand_btn = ""
        if has_children:
            expand_btn = f"""<button class='tree-expand' onclick='toggleTreeNode(this)'
                title='{child_count} nested items'>▶</button>"""

        html = f"""<li class='tree-node{"" if depth == 0 else " collapsed"}'>
            <div class='entity-item'>
                {expand_btn}
                <span class='entity-type-badge'>{item_type}</span>
                <a href='#' class='entity-name'
                   hx-get='{click_url}'
                   hx-target='#editor'>{item_name}</a>
                <button class='entity-delete' title='Delete'
                        hx-delete='{delete_url}'
                        hx-target='#entity-tree'
                        hx-confirm='Delete this {item_type}?'>×</button>
            </div>"""

        if children:
            html += "<ul class='entity-children'>"
            for child in children:
                html += render_tree_item(child, depth + 1)
            html += "</ul>"

        html += "</li>"
        return html

    # Render tree with entity types and actions
    html = "<ul class='entity-tree'>"
    for item in tree_data:
        html += render_tree_item(item)
    html += "</ul>"
    return HTMLResponse(html)


@router.post("/{project_id}/validate", response_class=HTMLResponse)
async def project_validate(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    """Validate all entities in the project against their schemas."""
    from pydantic import ValidationError

    if not validate_csrf_token(request):
        return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse("<div class='error'>Project not found</div>")

    state = get_project_state(project, project_states)
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


@router.get("/{project_id}/graph", response_class=HTMLResponse)
async def project_graph(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
    node_id: str | None = None,
) -> Response:
    """Graph visualization of project entities.

    Args:
        node_id: Optional. If provided, only show this entity and its descendants.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if not project:
        return RedirectResponse("/hub/")

    return _render_template(
        request=request,
        name="graph.html",
        context={
            "user": user,
            "project": project,
            "node_id": node_id,
        },
    )


@router.get("/{project_id}/api/graph")
async def project_graph_api(
    project_id: str,
    session: DbSession,
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

    state = get_project_state(project, project_states)

    # Build graph from hub's tree structure (uses TreeNode.children)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def truncate(text: str, max_len: int = 25) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "..."

    def add_node(tree_node: TreeNode, parent_vis_id: str | None = None) -> None:
        vis_id = tree_node.id
        if vis_id in node_ids:
            return
        node_ids.add(vis_id)

        nodes.append(
            {
                "id": vis_id,
                "label": truncate(tree_node.label, 25),
                "title": f"{tree_node.entity_type}: {tree_node.label}",
                "group": tree_node.entity_type,
            }
        )

        if parent_vis_id:
            edges.append({"from": parent_vis_id, "to": vis_id})

        # Process children stored in TreeNode.children
        for child in tree_node.children:
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


@router.get("/{project_id}/chat", response_class=HTMLResponse)
async def project_chat_page(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Full-page chat view for a project."""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse("<div class='error'>Project not found</div>")

    icon = "/hub/hub-static/images/metaseed-icon.svg"
    css = "/hub/hub-static/css/hub.css"
    js = "/hub/hub-static/js/hub.js"
    htmx = "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"
    post_url = f"/hub/projects/{project_id}/chat"
    back_url = f"/hub/projects/{project_id}"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Chat - {project.name}</title>
    <link rel="stylesheet" href="{css}">
    <script src="{htmx}"></script>
</head>
<body>
    <div class="hub-layout">
        <header class="hub-header">
            <a href="/hub/" class="hub-logo">
                <img src="{icon}" alt="" class="hub-logo-icon">
                <span>Metaseed Hub</span>
            </a>
            <nav class="hub-nav">
                <a href="{back_url}" class="nav-item">Back to Project</a>
                <span class="nav-sep">/</span>
                <span class="nav-item active">Chat</span>
            </nav>
        </header>
        <main class="hub-main chat-page">
            <div class="chat-header">
                <h2>{project.name} - Team Chat</h2>
            </div>
            <div id="chat-messages" class="chat-messages">
                <p class="chat-placeholder">Chat messages will appear here...</p>
            </div>
            <form class="chat-form"
                  hx-post="{post_url}"
                  hx-target="#chat-messages"
                  hx-swap="beforeend">
                <input type="text" name="message" placeholder="Type a message..."
                       autocomplete="off">
                <button type="submit" class="btn btn-primary">Send</button>
            </form>
        </main>
    </div>
    <script src="{js}"></script>
</body>
</html>"""
    return HTMLResponse(html)


@router.post("/{project_id}/chat", response_class=HTMLResponse)
async def project_chat(
    request: Request,
    project_id: str,
    user: CurrentUser,
    message: Annotated[str, Form()],
) -> HTMLResponse:
    """Post a chat message."""
    if not validate_csrf_token(request):
        return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

    # For now, just echo the message back
    html = f"<div class='chat-message'><strong>{user.name}:</strong> {message}</div>"
    return HTMLResponse(html)
