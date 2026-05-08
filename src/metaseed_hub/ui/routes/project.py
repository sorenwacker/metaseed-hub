"""Project routes for Hub UI."""

import copy
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from metaseed.ui.state import AppState
from sqlalchemy import select

from metaseed_hub.models import Project, SpecDraft, Workspace
from metaseed_hub.ui.dependencies import (
    CurrentUser,
    DbSession,
    get_project_for_user,
    verify_workspace_access,
)
from metaseed_hub.ui.helpers import (
    create_nested_nodes,
    get_project_state,
    get_tree_data_from_nodes,
    project_states,
    serialize_tree,
)
from metaseed_hub.ui.render import init_templates as _init_render_templates
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

logger = logging.getLogger("metaseed_hub")

router = APIRouter(prefix="/projects", tags=["projects"])


def init_templates(templates: Jinja2Templates) -> None:
    """Initialize templates reference."""
    _init_render_templates(templates)


@router.get("/new", response_class=HTMLResponse)
async def project_new(
    request: Request,
    session: DbSession,
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
                "source": "builtin",
            }
        )

    # Get spec drafts from this workspace
    drafts_result = await session.execute(
        select(SpecDraft).where(SpecDraft.workspace_id == workspace_id)
    )
    drafts = drafts_result.scalars().all()

    for draft in drafts:
        if draft.name:
            spec_data = draft.spec_data or {}
            profiles_data.append(
                {
                    "name": f"draft:{draft.id}",
                    "display_name": f"{draft.name} (Draft)",
                    "description": spec_data.get("description", ""),
                    "root_entity": spec_data.get("root_entity", "Investigation"),
                    "versions": [draft.version],
                    "latest_version": draft.version,
                    "source": "draft",
                }
            )

    return render_template(
        request=request,
        name="partials/project_form.html",
        context={
            "user": user,
            "workspace_id": workspace_id,
            "profiles": profiles_data,
            "nav_active": "home",
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

    from metaseed_hub.ui.helpers import validate_csrf_token

    if not validate_csrf_token(request, csrf_token):
        url = f"/hub/workspaces/{workspace_id}?error=csrf_validation_failed"
        return RedirectResponse(url, status_code=302)

    # Verify user has access to the workspace
    await verify_workspace_access(workspace_id, session, user)

    # Check if using a draft spec
    spec_draft_id = None
    if profile.startswith("draft:"):
        spec_draft_id = profile.replace("draft:", "")
        # Get the draft to use its name as the profile
        draft_result = await session.execute(select(SpecDraft).where(SpecDraft.id == spec_draft_id))
        draft = draft_result.scalar_one_or_none()
        if draft:
            profile = draft.name
            version = draft.version

    project = Project(
        workspace_id=workspace_id,
        name=name,
        profile=profile,
        version=version,
        spec_draft_id=spec_draft_id,
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
) -> Response:
    """Delete a project."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

    workspace_id = project.workspace_id
    await session.delete(project)
    await session.commit()

    # Return updated project grid
    result = await session.execute(select(Project).where(Project.workspace_id == workspace_id))
    projects = list(result.scalars().all())

    return render_template(
        request=request,
        name="partials/project_grid.html",
        context={"projects": projects},
    )


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

    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

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

    # Load example into project state (append, don't replace)
    state = get_project_state(project, project_states)
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


def _get_entity_info(state: AppState) -> list[dict[str, str]]:
    """Get entity type information including descriptions."""
    facade = state.get_or_create_facade()
    entity_info = []
    for entity_name in facade.entities:
        helper = getattr(facade, entity_name, None)
        if helper:
            entity_info.append(
                {
                    "name": entity_name,
                    "description": helper.description or "",
                }
            )
    return entity_info


def _build_project_context(project: Project) -> dict[str, Any]:
    """Build common context for project views.

    Args:
        project: Project model.

    Returns:
        Dictionary with state, tree_data, and entity_descriptions.
    """
    state = get_project_state(project, project_states)
    tree_data = get_tree_data_from_nodes(state)

    # Get descriptions for entity types
    facade = state.get_or_create_facade()
    entity_descriptions = {}
    for entity_name in facade.entities:
        helper = getattr(facade, entity_name, None)
        if helper:
            entity_descriptions[entity_name] = helper.description or ""

    return {
        "state": state,
        "tree_data": tree_data,
        "entity_descriptions": entity_descriptions,
    }


@router.get("/{project_id}", response_class=HTMLResponse)
async def project_editor(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Project editor - wraps metaseed UI with hub chrome."""
    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

    # Load workspace for breadcrumb
    workspace = await session.get(Workspace, project.workspace_id)

    # Build common project context
    ctx = _build_project_context(project)

    return render_template(
        request=request,
        name="project.html",
        context={
            "user": user,
            "project": project,
            "workspace": workspace,
            "state": ctx["state"],
            "root_types": ctx["state"].get_root_entity_types(),
            "tree_data": ctx["tree_data"],
            "entity_descriptions": ctx["entity_descriptions"],
            "nav_active": "home",
        },
    )


@router.get("/{project_id}/overview", response_class=HTMLResponse)
async def project_overview(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return the project overview panel (for HTMX navigation back)."""
    project = await get_project_for_user(project_id, session, user)

    # Build common project context
    ctx = _build_project_context(project)

    return render_template(
        request=request,
        name="partials/project_overview.html",
        context={
            "project": project,
            "root_types": ctx["state"].get_root_entity_types(),
            "tree_data": ctx["tree_data"],
            "entity_descriptions": ctx["entity_descriptions"],
        },
    )


@router.get("/{project_id}/tree", response_class=HTMLResponse)
async def project_tree(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return entity tree for a project."""
    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

    state = get_project_state(project, project_states)
    tree_data = get_tree_data_from_nodes(state)

    return render_template(
        request=request,
        name="partials/entity_tree.html",
        context={
            "tree_data": tree_data,
            "project_id": project_id,
        },
    )


@router.post("/{project_id}/validate", response_class=HTMLResponse)
async def project_validate(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    """Validate all entities in the project against their schemas."""
    from pydantic import ValidationError

    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

    state = get_project_state(project, project_states)
    facade = state.get_or_create_facade()

    errors: list[dict[str, Any]] = []

    for node_id, node in state.nodes_by_id.items():
        try:
            helper = getattr(facade, node.entity_type)
            data = node.instance.model_dump(exclude_none=True) if node.instance else {}
            # Re-validate by recreating - Pydantic validation runs here
            helper.create(**data)
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
                    <span class="entity-type-badge">{err["entity_type"]}</span>
                    <a href="#" class="entity-link"
                       hx-get="/hub/projects/{project_id}/entity/{err["node_id"]}"
                       hx-target="#editor"
                       hx-swap="innerHTML">{err["label"]}</a>
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
    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

    return render_template(
        request=request,
        name="graph.html",
        context={
            "user": user,
            "project": project,
            "node_id": node_id,
            "nav_active": "home",
        },
    )


@router.get("/{project_id}/api/graph")
async def project_graph_api(
    project_id: str,
    session: DbSession,
    user: CurrentUser,
    node_id: str | None = None,
) -> Response:
    """Return graph data for visualization (JSON API).

    Uses metaseed's build_graph to extract nodes and edges from instance data.

    Args:
        node_id: Optional. If provided, only include this node and its descendants.
    """
    from fastapi.responses import JSONResponse
    from metaseed.ui.services.graph import build_graph

    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

    state = get_project_state(project, project_states)

    # Use metaseed's graph builder which properly extracts nested entities
    graph_data = build_graph(state)

    return JSONResponse(content=graph_data)


@router.get("/{project_id}/chat", response_class=HTMLResponse)
async def project_chat_page(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Full-page chat view for a project."""
    # Verify user has access to this project
    project = await get_project_for_user(project_id, session, user)

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
    import html as html_module

    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Escape user content to prevent XSS
    safe_name = html_module.escape(user.name or "")
    safe_message = html_module.escape(message)
    html = f"<div class='chat-message'><strong>{safe_name}:</strong> {safe_message}</div>"
    return HTMLResponse(html)


@router.get("/{project_id}/export")
async def project_export(
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Export project data to Excel file.

    Uses metaseed's export service to generate an Excel workbook
    containing all entities in the project.
    """
    from fastapi.responses import StreamingResponse
    from metaseed.ui.services.export import export_to_bytes, generate_filename

    project = await get_project_for_user(project_id, session, user)
    state = get_project_state(project, project_states)

    # Generate Excel file using metaseed's export service
    excel_bytes = export_to_bytes(state)
    filename = generate_filename(state)

    # If no data, use project name for filename
    if not filename or filename == "export.xlsx":
        filename = f"{project.name.replace(' ', '_')}.xlsx"

    return StreamingResponse(
        excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/import", response_class=HTMLResponse)
async def project_import_form(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Show import form for uploading ISA-JSON files."""
    project = await get_project_for_user(project_id, session, user)

    return render_template(
        request=request,
        name="partials/import_form.html",
        context={
            "project_id": project_id,
            "project": project,
        },
    )


@router.post("/{project_id}/import", response_class=HTMLResponse)
async def project_import(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Import ISA-JSON file into project.

    Uses metaseed's ISAImporter to parse ISA-JSON files and create
    entities in the project.
    """
    import tempfile

    from fastapi import UploadFile
    from metaseed.importers.isa import ISAImporter
    from metaseed.models import get_model

    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    project = await get_project_for_user(project_id, session, user)
    state = get_project_state(project, project_states)
    facade = state.get_or_create_facade()

    # Get uploaded file from form
    form = await request.form()
    file = form.get("file")
    if not file or not isinstance(file, UploadFile):
        return render_template(
            request=request,
            name="partials/import_form.html",
            context={
                "project_id": project_id,
                "project": project,
                "error": "Please select a file to import.",
            },
        )

    filename = file.filename or ""
    content = await file.read()

    if not filename.endswith(".json"):
        return render_template(
            request=request,
            name="partials/import_form.html",
            context={
                "project_id": project_id,
                "project": project,
                "error": "Unsupported file type. Please upload an ISA-JSON file (.json).",
            },
        )

    try:
        importer = ISAImporter()

        # Write to temp file for importer
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        result = importer.import_json(tmp_path)
        Path(tmp_path).unlink()

        # Process imported data
        if result.investigation and "Investigation" in facade.entities:
            helper = facade.Investigation
            inv_data = result.investigation.copy()

            # Add studies if present
            if result.studies and "studies" in helper.nested_fields:
                inv_data["studies"] = result.studies

            # Add persons/contacts
            if result.persons:
                for field_name in ["persons", "contacts", "people"]:
                    if field_name in helper.nested_fields:
                        inv_data[field_name] = result.persons
                        break

            # Create instance and add to state
            Model = get_model("Investigation", state.version, profile=state.profile)
            instance = Model(**inv_data)
            node = state.add_node("Investigation", instance)

            # Create nested child nodes
            example_data_copy = copy.deepcopy(inv_data)
            create_nested_nodes(state, facade, node, "Investigation", example_data_copy)

            # Save to database
            from sqlalchemy.orm.attributes import flag_modified

            project.data = serialize_tree(state)
            flag_modified(project, "data")
            session.add(project)
            await session.commit()

            return render_template(
                request=request,
                name="partials/import_form.html",
                context={
                    "project_id": project_id,
                    "project": project,
                    "success": f"Successfully imported {filename}",
                },
            )

        return render_template(
            request=request,
            name="partials/import_form.html",
            context={
                "project_id": project_id,
                "project": project,
                "error": "No valid Investigation found in the ISA-JSON file.",
            },
        )

    except Exception as e:
        logger.exception("Import failed")
        return render_template(
            request=request,
            name="partials/import_form.html",
            context={
                "project_id": project_id,
                "project": project,
                "error": f"Import failed: {e!s}",
            },
        )
