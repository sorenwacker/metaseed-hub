"""Entity routes for Hub UI."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from metaseed_hub.ui.dependencies import CurrentUser, DbSession, get_dataset_for_user
from metaseed_hub.ui.forms import extract_entity_values
from metaseed_hub.ui.helpers import (
    build_entity_form_context,
    ensure_dataset_facade,
    save_dataset_state,
)
from metaseed_hub.ui.render import init_templates as _init_render_templates
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

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

    state = await ensure_dataset_facade(dataset, session)
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

    Uses model_construct to skip Pydantic validation, allowing entities
    to be saved without all required nested entities. This enables the
    "save first, add related entities later" workflow.
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

    try:
        dataset = await get_dataset_for_user(dataset_id, session, user)
        state = await ensure_dataset_facade(dataset, session)
        facade = state.get_or_create_facade()
        helper = getattr(facade, entity_type)
    except Exception as e:
        logger.exception(f"Failed to load dataset or facade for {entity_type}")
        return HTMLResponse(f"<div class='error'>Failed to load: {e}</div>")

    # Get existing values if updating
    existing_node = state.nodes_by_id.get(node_id) if node_id else None
    existing_values = None
    if existing_node and hasattr(existing_node.instance, "model_dump"):
        existing_values = existing_node.instance.model_dump(exclude_none=True)

    # Extract and type-convert form values
    values = extract_entity_values(form_data, helper, existing_values)

    # Create instance using model_construct (skips validation)
    try:
        model_class = helper._model
        instance = model_class.model_construct(**values)
    except Exception as e:
        logger.exception(f"Failed to construct {entity_type}")
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
                "error": f"Failed to create: {e}",
                **form_context,
            },
        )

    # Update or create node
    if node_id and node_id in state.nodes_by_id:
        node = state.nodes_by_id[node_id]
        node.instance = instance
        label = helper.get_label(instance)
        if label:
            node.label = label
        success_msg = f"{entity_type} updated successfully."
    else:
        node = state.add_node(entity_type, instance, parent_id=parent_id)
        node_id = node.id
        success_msg = f"{entity_type} created successfully."

    # Save to database
    try:
        await save_dataset_state(session, dataset, state)
    except Exception as e:
        logger.exception(f"Failed to save {entity_type}")
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
                "error": f"Failed to save: {e}",
                **form_context,
            },
        )

    # Return success response
    form_context = build_entity_form_context(state, helper, node_id, parent_id)
    form_context["values"] = values

    response = render_template(
        request=request,
        name="partials/entity_form.html",
        context={
            "dataset_id": dataset_id,
            "entity_type": entity_type,
            "node_id": node_id,
            "parent_id": parent_id,
            "success": success_msg,
            **form_context,
        },
    )
    response.headers["HX-Trigger"] = "entityChanged"
    return response


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
    state = await ensure_dataset_facade(dataset, session)

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

    state = await ensure_dataset_facade(dataset, session)

    if node_id not in state.nodes_by_id:
        return HTMLResponse("<div class='error'>Entity not found</div>")

    # Delete the node
    state.delete_node(node_id)

    # Save to database
    await save_dataset_state(session, dataset, state)

    # Return updated tree + out-of-band editor update
    tree_data = state.get_tree_data()

    return render_template(
        request=request,
        name="partials/entity_tree_simple.html",
        context={
            "dataset_id": dataset_id,
            "tree_data": tree_data,
        },
    )
