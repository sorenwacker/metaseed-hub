"""Entity routes for Hub UI."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from metaseed.ui.state import AppState

from metaseed_hub.ui.dependencies import CurrentUser, DbSession, get_project_for_user
from metaseed_hub.ui.forms import extract_entity_values
from metaseed_hub.ui.helpers import (
    build_entity_form_context,
    get_project_state,
    save_project_state,
)
from metaseed_hub.ui.render import init_templates as _init_render_templates
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

logger = logging.getLogger("metaseed_hub")

router = APIRouter(prefix="/projects/{project_id}", tags=["entities"])

# Project state cache - maps project_id to AppState
project_states: dict[str, AppState] = {}


def init_templates(templates: Jinja2Templates) -> None:
    """Initialize templates reference."""
    _init_render_templates(templates)


@router.get("/form/{entity_type}", response_class=HTMLResponse)
async def project_entity_form(
    request: Request,
    project_id: str,
    entity_type: str,
    session: DbSession,
    user: CurrentUser,
    node_id: str | None = None,
    parent_id: str | None = None,
    parent_field: str | None = None,
) -> Response:
    """Return form for creating or editing an entity."""
    project = await get_project_for_user(project_id, session, user)

    state = get_project_state(project, project_states)
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, entity_type)
    except AttributeError:
        return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

    form_context = build_entity_form_context(state, helper, node_id, parent_id)

    return render_template(
        request=request,
        name="partials/entity_form.html",
        context={
            "project_id": project_id,
            "entity_type": entity_type,
            "node_id": node_id,
            "parent_id": parent_id,
            "parent_field": parent_field,
            **form_context,
        },
    )


@router.post("/entities", response_class=HTMLResponse)
async def project_entity_create(
    request: Request,
    project_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Create or update an entity."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    form_data = await request.form()
    entity_type = str(form_data.get("_entity_type", ""))
    node_id = str(form_data.get("_node_id", "")) or None
    parent_id = str(form_data.get("_parent_id", "")) or None

    if not entity_type:
        return HTMLResponse("<div class='error'>Missing entity type</div>")

    project = await get_project_for_user(project_id, session, user)

    state = get_project_state(project, project_states)
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, entity_type)
    except AttributeError:
        return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

    logger.info(f"Entity create/update: entity_type={entity_type}, node_id={node_id}")
    logger.info(f"Form data keys: {list(form_data.keys())}")

    # Get existing values if updating
    existing_node = state.nodes_by_id.get(node_id) if node_id else None
    logger.info(f"Existing node: {existing_node is not None}")

    existing_values = None
    if existing_node and hasattr(existing_node.instance, "model_dump"):
        existing_values = existing_node.instance.model_dump(exclude_none=True)

    # Extract and type-convert form values
    values = extract_entity_values(form_data, helper, existing_values)
    logger.info(f"Final values for create: {values}")

    # Create or update instance
    try:
        instance = helper.create(**values)
        if hasattr(instance, "model_dump"):
            logger.info(f"Created instance: {instance.model_dump(exclude_none=True)}")

        if node_id and node_id in state.nodes_by_id:
            # Update existing node
            node = state.nodes_by_id[node_id]
            node.instance = instance
            # Update label from instance using helper's get_label method
            label = helper.get_label(instance)
            if label:
                node.label = label
            success_msg = f"{entity_type} updated successfully."
        else:
            # Create new node
            node = state.add_node(entity_type, instance, parent_id=parent_id)
            node_id = node.id
            success_msg = f"{entity_type} created successfully."

        # Save to database
        await save_project_state(session, project, state)

        # Build form context with updated values from saved instance
        form_context = build_entity_form_context(state, helper, node_id, parent_id)
        # Override values with the freshly saved instance data
        form_context["values"] = instance.model_dump(exclude_none=True)

        response = render_template(
            request=request,
            name="partials/entity_form.html",
            context={
                "project_id": project_id,
                "entity_type": entity_type,
                "node_id": node_id,
                "parent_id": parent_id,
                "success": success_msg,
                **form_context,
            },
        )
        response.headers["HX-Trigger"] = "entityChanged"
        return response

    except Exception as e:
        logger.exception(f"Error creating/updating {entity_type}")

        # Build form context and preserve submitted values for error display
        form_context = build_entity_form_context(state, helper, node_id, parent_id)
        form_context["values"] = values  # Keep submitted values for user to correct

        return render_template(
            request=request,
            name="partials/entity_form.html",
            context={
                "project_id": project_id,
                "entity_type": entity_type,
                "node_id": node_id,
                "parent_id": parent_id,
                "error": str(e),
                **form_context,
            },
        )


@router.get("/entity/{node_id}", response_class=HTMLResponse)
async def project_entity_edit(
    request: Request,
    project_id: str,
    node_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return form for editing an existing entity."""
    project = await get_project_for_user(project_id, session, user)
    state = get_project_state(project, project_states)

    if node_id not in state.nodes_by_id:
        return HTMLResponse("<div class='error'>Entity not found</div>")

    node = state.nodes_by_id[node_id]
    entity_type = node.entity_type
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, entity_type)
    except AttributeError:
        return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

    form_context = build_entity_form_context(state, helper, node_id, node.parent_id)

    return render_template(
        request=request,
        name="partials/entity_form.html",
        context={
            "project_id": project_id,
            "entity_type": entity_type,
            "node_id": node_id,
            "parent_id": node.parent_id,
            **form_context,
        },
    )


@router.delete("/entity/{node_id}", response_class=HTMLResponse)
async def project_entity_delete(
    request: Request,
    project_id: str,
    node_id: str,
    session: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    """Delete an entity."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    project = await get_project_for_user(project_id, session, user)

    state = get_project_state(project, project_states)

    if node_id not in state.nodes_by_id:
        return HTMLResponse("<div class='error'>Entity not found</div>")

    # Delete the node
    state.delete_node(node_id)

    # Save to database
    await save_project_state(session, project, state)

    # Return updated tree + out-of-band editor update
    tree_data = state.get_tree_data()

    # Build editor placeholder (out-of-band swap)
    editor_html = """<div id="editor" hx-swap-oob="innerHTML">
        <div class="editor-placeholder">
            <p>Select an entity or create a new one.</p>
        </div>
    </div>"""

    if not tree_data:
        return HTMLResponse("<div class='empty-state'><p>No entities yet.</p></div>" + editor_html)

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
    html += editor_html
    return HTMLResponse(html)
