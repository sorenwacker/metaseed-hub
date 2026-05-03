"""Entity routes for Hub UI."""

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from metaseed.ui.state import AppState

from metaseed_hub.ui.dependencies import CurrentUser, DbSession, get_project_by_id
from metaseed_hub.ui.helpers import (
    CSRF_TOKEN_COOKIE,
    build_entity_form_context,
    get_or_create_csrf_token,
    get_project_state,
    save_project_state,
    validate_csrf_token,
)

logger = logging.getLogger("metaseed_hub")

router = APIRouter(prefix="/projects/{project_id}", tags=["entities"])

# Templates reference, initialized by init_templates()
_templates: Jinja2Templates | None = None

# Project state cache - maps project_id to AppState
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
    project = await get_project_by_id(project_id, session)

    state = get_project_state(project, project_states)
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, entity_type)
    except AttributeError:
        return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

    form_context = build_entity_form_context(state, helper, node_id, parent_id)

    return _render_template(
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
    if not validate_csrf_token(request):
        return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

    form_data = await request.form()
    entity_type = str(form_data.get("_entity_type", ""))
    node_id = str(form_data.get("_node_id", "")) or None
    parent_id = str(form_data.get("_parent_id", "")) or None

    if not entity_type:
        return HTMLResponse("<div class='error'>Missing entity type</div>")

    project = await get_project_by_id(project_id, session)

    state = get_project_state(project, project_states)
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, entity_type)
    except AttributeError:
        return HTMLResponse(f"<div class='error'>Unknown entity type: {entity_type}</div>")

    # Collect form values, converting types as needed
    values: dict[str, str | int | float | bool | None] = {}

    logger.info(f"Entity create/update: entity_type={entity_type}, node_id={node_id}")
    logger.info(f"Form data keys: {list(form_data.keys())}")

    # If updating existing node, start with existing values
    existing_node = state.nodes_by_id.get(node_id) if node_id else None
    logger.info(f"Existing node: {existing_node is not None}")
    if existing_node and hasattr(existing_node.instance, "model_dump"):
        existing_data = existing_node.instance.model_dump(exclude_none=True)
        # Copy non-nested existing values
        for field_name in helper.all_fields:
            info = helper.field_info(field_name)
            if info.get("type") not in ("list", "entity") and field_name in existing_data:
                values[field_name] = existing_data[field_name]

    # Override with form values
    for field_name in helper.all_fields:
        raw_value = form_data.get(field_name)
        if raw_value is None:
            continue
        if raw_value == "":
            # Empty string clears the field (but keep inherited fields)
            if field_name in values and field_name.endswith("_id"):
                continue
            values[field_name] = None
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

    # Remove None values for create
    values = {k: v for k, v in values.items() if v is not None}
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

        # Build form context with updated values from saved instance
        form_context = build_entity_form_context(state, helper, node_id, parent_id)
        # Override values with the freshly saved instance data
        form_context["values"] = instance.model_dump(exclude_none=True)

        response = _render_template(
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

        return _render_template(
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
    project = await get_project_by_id(project_id, session)
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

    return _render_template(
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
    if not validate_csrf_token(request):
        return HTMLResponse("<div class='error'>CSRF validation failed</div>", status_code=403)

    project = await get_project_by_id(project_id, session)

    state = get_project_state(project, project_states)

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
