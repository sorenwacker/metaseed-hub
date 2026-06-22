"""Entity routes for Hub UI."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from metaseed_hub.ui.dependencies import CurrentUser, DbSession, get_dataset_for_user
from metaseed_hub.ui.forms import extract_entity_values
from metaseed_hub.ui.helpers import build_entity_form_context
from metaseed_hub.ui.render import init_templates as _init_render_templates
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error
from metaseed_hub.ui.services import EntityService, EntityServiceError

logger = logging.getLogger("metaseed_hub")

router = APIRouter(prefix="/datasets/{dataset_id}", tags=["entities"])


def init_templates(templates: Jinja2Templates) -> None:
    """Initialize templates reference."""
    _init_render_templates(templates)


@router.get("/form/{entity_type}", response_class=HTMLResponse)
async def dataset_entity_form(
    request: Request,
    dataset_id: str,
    entity_type: str,
    session: DbSession,
    user: CurrentUser,
    node_id: str | None = None,
    parent_id: str | None = None,
    parent_field: str | None = None,
) -> Response:
    """Return form for creating or editing an entity."""
    dataset = await get_dataset_for_user(dataset_id, session, user)

    service = EntityService(session, dataset)
    try:
        state = await service.ensure_state()
    except EntityServiceError as e:
        return HTMLResponse(f"<div class='error'>{e.user_message}</div>")

    try:
        helper = service.get_helper(entity_type)
    except EntityServiceError as e:
        return HTMLResponse(f"<div class='error'>{e.user_message}</div>")

    form_context = build_entity_form_context(state, helper, node_id, parent_id)

    return render_template(
        request=request,
        name="partials/entity_form.html",
        context={
            "dataset_id": dataset_id,
            "entity_type": entity_type,
            "node_id": node_id,
            "parent_id": parent_id,
            "parent_field": parent_field,
            **form_context,
        },
    )


@router.post("/entities", response_class=HTMLResponse)
async def dataset_entity_create(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Create or update an entity.

    Uses EntityService which saves entities even when validation fails.
    Validation errors are returned as warnings, not blockers.
    """
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

    dataset = await get_dataset_for_user(dataset_id, session, user)
    service = EntityService(session, dataset)

    try:
        state = await service.ensure_state()
        helper = service.get_helper(entity_type)
    except EntityServiceError as e:
        logger.error(f"Failed to load dataset or facade for {entity_type}: {e.message}")
        return HTMLResponse(f"<div class='error'>{e.user_message}</div>")

    # Get existing values if updating
    existing_node = state.nodes_by_id.get(node_id) if node_id else None
    existing_values = None
    if existing_node and hasattr(existing_node.instance, "model_dump"):
        existing_values = existing_node.instance.model_dump(exclude_none=True)

    # Extract and type-convert form values
    values = extract_entity_values(form_data, helper, existing_values)

    # Create or update entity using service
    result = await service.create_or_update_entity(
        entity_type=entity_type,
        values=values,
        node_id=node_id,
        parent_id=parent_id,
    )

    if not result.success:
        form_context = build_entity_form_context(state, helper, node_id, parent_id)
        form_context["values"] = values
        return render_template(
            request=request,
            name="partials/entity_form.html",
            context={
                "dataset_id": dataset_id,
                "entity_type": entity_type,
                "node_id": node_id,
                "parent_id": parent_id,
                "error": result.error_message,
                **form_context,
            },
        )

    # Build success response
    is_update = node_id is not None and existing_node is not None
    success_msg = f"{entity_type} {'updated' if is_update else 'created'} successfully."

    form_context = build_entity_form_context(state, helper, result.node_id, parent_id)
    form_context["values"] = values

    response = render_template(
        request=request,
        name="partials/entity_form.html",
        context={
            "dataset_id": dataset_id,
            "entity_type": entity_type,
            "node_id": result.node_id,
            "parent_id": parent_id,
            "success": success_msg,
            "validation_warnings": result.validation_errors,
            **form_context,
        },
    )
    response.headers["HX-Trigger"] = "entityChanged"
    return response


@router.post("/entities/validate", response_class=HTMLResponse)
async def dataset_entity_validate(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Validate entity data without saving.

    Returns validation result as HTML for display in the form.
    """
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    form_data = await request.form()
    entity_type = str(form_data.get("_entity_type", ""))
    node_id = str(form_data.get("_node_id", "")) or None

    if not entity_type:
        return HTMLResponse("<div class='error-message'>Missing entity type</div>")

    dataset = await get_dataset_for_user(dataset_id, session, user)
    service = EntityService(session, dataset)

    try:
        state = await service.ensure_state()
        helper = service.get_helper(entity_type)
    except EntityServiceError as e:
        return HTMLResponse(f"<div class='error-message'>{e.user_message}</div>")

    # Get existing values if validating existing entity
    existing_node = state.nodes_by_id.get(node_id) if node_id else None
    existing_values = None
    if existing_node and hasattr(existing_node.instance, "model_dump"):
        existing_values = existing_node.instance.model_dump(exclude_none=True)

    # Extract and type-convert form values
    values = extract_entity_values(form_data, helper, existing_values)

    # Validate without saving
    errors = await service.validate_entity(entity_type, values)

    if not errors:
        return HTMLResponse(
            "<div class='success-message'>Validation passed. No errors found.</div>"
        )

    # Format errors as list
    error_html = "<div class='warning-message'><strong>Validation issues:</strong><ul>"
    for error in errors:
        error_html += f"<li>{error}</li>"
    error_html += "</ul></div>"
    return HTMLResponse(error_html)


@router.get("/entity/{node_id}", response_class=HTMLResponse)
async def dataset_entity_edit(
    request: Request,
    dataset_id: str,
    node_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return form for editing an existing entity."""
    dataset = await get_dataset_for_user(dataset_id, session, user)

    service = EntityService(session, dataset)
    try:
        state = await service.ensure_state()
    except EntityServiceError as e:
        return HTMLResponse(f"<div class='error'>{e.user_message}</div>")

    if node_id not in state.nodes_by_id:
        return HTMLResponse("<div class='error'>Entity not found</div>")

    node = state.nodes_by_id[node_id]
    entity_type = node.entity_type

    try:
        helper = service.get_helper(entity_type)
    except EntityServiceError as e:
        return HTMLResponse(f"<div class='error'>{e.user_message}</div>")

    form_context = build_entity_form_context(state, helper, node_id, node.parent_id)

    return render_template(
        request=request,
        name="partials/entity_form.html",
        context={
            "dataset_id": dataset_id,
            "entity_type": entity_type,
            "node_id": node_id,
            "parent_id": node.parent_id,
            **form_context,
        },
    )


@router.delete("/entity/{node_id}", response_class=HTMLResponse)
async def dataset_entity_delete(
    request: Request,
    dataset_id: str,
    node_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Delete an entity."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    dataset = await get_dataset_for_user(dataset_id, session, user)

    service = EntityService(session, dataset)
    try:
        state = await service.ensure_state()
    except EntityServiceError as e:
        return HTMLResponse(f"<div class='error'>{e.user_message}</div>")

    result = await service.delete_entity(node_id)

    if not result.success:
        return HTMLResponse(f"<div class='error'>{result.error_message}</div>")

    # Return updated tree
    tree_data = state.get_tree_data()

    return render_template(
        request=request,
        name="partials/entity_tree_simple.html",
        context={
            "dataset_id": dataset_id,
            "tree_data": tree_data,
        },
    )
