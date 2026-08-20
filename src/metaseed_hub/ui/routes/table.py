"""Inline table routes for Hub UI.

Handles CRUD operations for inline entity tables and primitive lists.
"""

import logging
from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import OptionalUser, get_dataset_state_for_mutation
from metaseed_hub.ui.forms import parse_form_field
from metaseed_hub.ui.helpers import build_inline_tables, save_dataset_state
from metaseed_hub.ui.metaseed_ui import AppState
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.routes.table_rows import (
    PRIMITIVE_TYPES,
    _addresses_an_item,
    _build_entity_row_html,
    _build_primitive_row_html,
    _get_default_values,
    _handle_primitive_list_row,
)

router = APIRouter(tags=["table"])

logger = logging.getLogger("metaseed_hub")


@router.post(
    "/datasets/{dataset_id}/table/{parent_node_id}/{field_name}/row",
    response_class=HTMLResponse,
)
async def add_table_row(
    request: Request,
    dataset_id: str,
    parent_node_id: str,
    field_name: str,
    dataset_state: Annotated[tuple[Dataset, AppState], Depends(get_dataset_state_for_mutation)],
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Add a new row to an inline table.

    Handles both primitive lists (strings, integers, etc.) and entity lists.
    For primitive lists, adds a new empty value to the list.
    For entity lists, creates a new child entity with default values.
    """
    logger.info(f"add_table_row: parent_node_id={parent_node_id}, field_name={field_name}")

    dataset, state = dataset_state

    if parent_node_id not in state.nodes_by_id:
        logger.warning(f"add_table_row: parent_node_id {parent_node_id} not found")
        return HTMLResponse("<tr><td>Parent entity not found</td></tr>")

    parent_node = state.nodes_by_id[parent_node_id]
    facade = state.get_or_create_facade()

    if facade is None:
        logger.error("add_table_row: facade is None")
        return HTMLResponse("<tr><td>Error: Could not load profile</td></tr>")

    # Get the nested entity type from the parent's field info
    try:
        parent_helper = getattr(facade, parent_node.entity_type)
        if field_name not in set(parent_helper.all_fields):
            # field_info raises KeyError, which this try does not catch.
            return HTMLResponse("<tr><td>Invalid field</td></tr>")
        field_info = parent_helper.field_info(field_name)
        nested_type = field_info.get("items")
        if not nested_type:
            return HTMLResponse("<tr><td>Invalid field</td></tr>")

        # Handle primitive list types (list of strings, integers, etc.)
        if nested_type.lower() in PRIMITIVE_TYPES:
            row_idx, current_list = _handle_primitive_list_row(parent_node, field_name)

            # Update parent instance with new list (use model_construct to skip validation)
            update_data = parent_node.instance.model_dump(exclude_none=True)
            update_data[field_name] = current_list
            model_class = parent_helper.model
            updated_instance = model_class.model_construct(**update_data)
            state.update_node(parent_node_id, updated_instance)

            # Save to database
            await save_dataset_state(session, dataset, state, user)

            # Return row HTML for primitive value
            html = _build_primitive_row_html(
                dataset_id, parent_node_id, field_name, row_idx, nested_type
            )

            response = HTMLResponse(html)
            response.headers["HX-Trigger"] = "entityChanged"
            return response

        nested_helper = getattr(facade, nested_type)
    except AttributeError:
        return HTMLResponse("<tr><td>Unknown entity type</td></tr>")

    return await _add_entity_list_row(
        session,
        dataset,
        state,
        dataset_id,
        parent_node_id,
        field_name,
        nested_type,
        nested_helper,
        user,
    )


async def _add_entity_list_row(
    session: AsyncSession,
    dataset: Dataset,
    state: AppState,
    dataset_id: str,
    parent_node_id: str,
    field_name: str,
    nested_type: str,
    nested_helper: Any,
    user: OptionalUser,
) -> HTMLResponse:
    """Append a default child entity to an entity-list field and return its row HTML.

    The entity-list counterpart to ``_handle_primitive_list_row``: it creates the
    child TreeNode with default values, persists the dataset, and renders the new
    table row.
    """
    parent_node = state.nodes_by_id[parent_node_id]

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
    default_values = _get_default_values(nested_helper, parent_node, parent_identifier)

    # Create instance with defaults (use model_construct to skip validation)
    model_class = nested_helper.model
    instance = model_class.model_construct(**default_values)

    # Add the child through the facade (skip_validation lets an incomplete draft
    # row persist). This writes to the facade -- the source of truth -- rather
    # than only the TreeNode cache, so the row is not lost on save. add_node also
    # keeps the cache consistent and returns the wrapper for rendering.
    child_node = state.add_node(
        nested_type, instance, parent_id=parent_node_id, skip_validation=True
    )

    # Save to database
    await save_dataset_state(session, dataset, state, user)

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

    # Get instance data for cell values
    instance_data = {}
    if hasattr(instance, "model_dump"):
        instance_data = instance.model_dump(exclude_none=True)

    # Determine inherited columns (reference to parent)
    parent_type_lower = parent_node.entity_type.lower()
    inherited_cols = set()
    for col in columns:
        if col.endswith("_id"):
            ref_type = col[:-3]
            if ref_type == parent_type_lower:
                inherited_cols.add(col)

    html = _build_entity_row_html(
        dataset_id,
        field_name,
        row_idx,
        child_node,
        nested_type,
        columns,
        column_types,
        inherited_cols,
        instance_data,
    )

    response = HTMLResponse(html)
    response.headers["HX-Trigger"] = "entityChanged"
    return response


@router.post("/datasets/{dataset_id}/table/{node_id}/cell")
async def update_table_cell(
    request: Request,
    dataset_id: str,
    node_id: str,
    dataset_state: Annotated[tuple[Dataset, AppState], Depends(get_dataset_state_for_mutation)],
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Update a single cell value in an inline table."""
    dataset, state = dataset_state

    if node_id not in state.nodes_by_id:
        return HTMLResponse(status_code=404)

    node = state.nodes_by_id[node_id]
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, node.entity_type)
    except AttributeError:
        return HTMLResponse(status_code=400)

    form_data = await request.form()

    # Get current values
    current_values = {}
    if hasattr(node.instance, "model_dump"):
        current_values = node.instance.model_dump(exclude_none=True)

    # Update with new values from form
    valid_fields = set(helper.all_fields)
    for field_name, raw_value in form_data.items():
        if field_name.startswith("_"):
            continue
        if field_name not in valid_fields:
            # A caller-controlled name the entity does not have: skipping it
            # matches the valid-field gate; field_info would KeyError -> 500.
            continue
        if not isinstance(raw_value, str):
            continue
        if raw_value is None:
            continue
        if raw_value == "":
            # An explicit empty submission clears the cell. Skipping it would
            # keep the old value while the UI shows an empty cell.
            current_values.pop(field_name, None)
            continue

        raw_str = str(raw_value)
        info = helper.field_info(field_name)
        field_type = info.get("type", "string")

        # Reuse the shared coercion helper, which falls back to the raw string on
        # a bad numeric value instead of letting ValueError 500 the request.
        try:
            current_values[field_name] = parse_form_field(raw_str, field_type)
        except ValueError:
            current_values[field_name] = raw_str

    model_class = helper.model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)
    await save_dataset_state(session, dataset, state, user)

    return Response(
        status_code=200,
        headers={"HX-Trigger": "entityChanged"},
    )


@router.post(
    "/datasets/{dataset_id}/table/{node_id}/primitive/{field_name}/{idx}",
    response_class=HTMLResponse,
)
async def update_primitive_list_item(
    request: Request,
    dataset_id: str,
    node_id: str,
    field_name: str,
    idx: int,
    dataset_state: Annotated[tuple[Dataset, AppState], Depends(get_dataset_state_for_mutation)],
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Update a primitive list item value."""
    dataset, state = dataset_state

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

    if field_name not in set(helper.all_fields):
        return HTMLResponse(status_code=400)

    current_list = current_values.get(field_name, []) or []
    if not _addresses_an_item(current_list, idx):
        return HTMLResponse(status_code=400)
    current_list[idx] = new_value
    current_values[field_name] = current_list

    # Create updated instance (skip validation)
    model_class = helper.model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state, user)

    return HTMLResponse(status_code=200, headers={"HX-Trigger": "entityChanged"})


def _inline_table_fragment(
    request: Request,
    state: AppState,
    dataset_id: str,
    node_id: str,
    field_name: str,
) -> Response:
    """The one inline table, re-rendered from current state.

    A row delete used to remove only its own ``<tr>`` (``hx-swap="delete"``)
    while the remaining rows kept their original ``_idx`` in every hx URL — so
    the next edit wrote to the wrong item, and an edit past the new end was
    silently dropped. Rows are positional; after a structural change the whole
    table is the smallest thing that is still correct.
    """
    node = state.nodes_by_id[node_id]
    facade = state.get_or_create_facade()
    helper = getattr(facade, node.entity_type)
    info = helper.field_info(field_name)
    field = {
        "name": field_name,
        "type": info.get("type", "string"),
        "item_type": info.get("items"),
        "is_nested": True,
    }
    inline_tables = build_inline_tables(state, node_id, [field])
    response = render_template(
        request=request,
        name="partials/inline_table.html",
        context={
            "field": field,
            "inline_tables": inline_tables,
            "dataset_id": dataset_id,
            "node_id": node_id,
        },
    )
    response.headers["HX-Trigger"] = "entityChanged"
    return response


@router.delete(
    "/datasets/{dataset_id}/table/{node_id}/primitive/{field_name}/{idx}",
    response_class=HTMLResponse,
)
async def delete_primitive_list_item(
    request: Request,
    dataset_id: str,
    node_id: str,
    field_name: str,
    idx: int,
    dataset_state: Annotated[tuple[Dataset, AppState], Depends(get_dataset_state_for_mutation)],
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Delete a primitive list item, returning the re-rendered table."""
    dataset, state = dataset_state

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

    if field_name not in set(helper.all_fields):
        return HTMLResponse(status_code=400)

    current_list = current_values.get(field_name, []) or []
    if not _addresses_an_item(current_list, idx):
        return HTMLResponse(status_code=400)
    current_list.pop(idx)
    current_values[field_name] = current_list

    # Create updated instance (skip validation)
    model_class = helper.model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state, user)

    # The whole table, not a row removal: the surviving rows' indices shifted.
    return _inline_table_fragment(request, state, dataset_id, node_id, field_name)


@router.post(
    "/datasets/{dataset_id}/table/{node_id}/single/{field_name}",
    response_class=HTMLResponse,
)
async def update_single_entity_field(
    request: Request,
    dataset_id: str,
    node_id: str,
    field_name: str,
    dataset_state: Annotated[tuple[Dataset, AppState], Depends(get_dataset_state_for_mutation)],
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Update a single entity field (e.g., measurement_type, technology_type).

    Single entity fields are nested objects that are not lists - they contain
    a single instance of another entity type embedded in the parent.
    """
    dataset, state = dataset_state

    if node_id not in state.nodes_by_id:
        logger.warning(f"update_single_entity_field: node_id {node_id} not found in state")
        return HTMLResponse(status_code=404)

    node = state.nodes_by_id[node_id]
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, node.entity_type)
    except AttributeError:
        return HTMLResponse(status_code=400)

    # field_name is caller-controlled; field_info raises KeyError -> 500 for a
    # name the entity does not have.
    if field_name not in set(helper.all_fields):
        return HTMLResponse(status_code=400)

    # Get field info to understand nested type
    field_info = helper.field_info(field_name)
    nested_type = field_info.get("items")
    if not nested_type:
        return HTMLResponse(status_code=400)

    # Get form data
    form_data = await request.form()

    # Build the nested entity data from form fields
    nested_data: dict[str, Any] = {}
    try:
        nested_helper = getattr(facade, nested_type)
        for fname in nested_helper.all_fields:
            if fname in form_data:
                raw_value = str(form_data.get(fname, ""))
                if raw_value:
                    info = nested_helper.field_info(fname)
                    ftype = info.get("type", "string")
                    try:
                        nested_data[fname] = parse_form_field(raw_value, ftype)
                    except ValueError:
                        nested_data[fname] = raw_value
    except AttributeError:
        # Nested type not found, just store raw form data
        for key, value in form_data.items():
            if not key.startswith("_") and value:
                nested_data[key] = str(value)

    # Get current parent values and update the single entity field
    current_values: dict[str, Any] = {}
    if hasattr(node.instance, "model_dump"):
        current_values = node.instance.model_dump(exclude_none=True)

    # Merge new values with existing nested field data (don't replace entirely)
    existing_nested = current_values.get(field_name, {}) or {}
    if isinstance(existing_nested, dict):
        existing_nested.update(nested_data)
        current_values[field_name] = existing_nested
    else:
        current_values[field_name] = nested_data

    # Create updated parent instance (skip validation)
    model_class = helper.model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state, user)

    return HTMLResponse(
        status_code=200,
        headers={"HX-Trigger": "entityChanged"},
    )


@router.delete(
    "/datasets/{dataset_id}/table/{node_id}/single/{field_name}",
    response_class=HTMLResponse,
)
async def delete_single_entity_field(
    request: Request,
    dataset_id: str,
    node_id: str,
    field_name: str,
    dataset_state: Annotated[tuple[Dataset, AppState], Depends(get_dataset_state_for_mutation)],
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Clear/delete a single entity field.

    Sets the field to None/empty, removing the nested entity data.
    """
    dataset, state = dataset_state

    if node_id not in state.nodes_by_id:
        return HTMLResponse(status_code=404)

    node = state.nodes_by_id[node_id]
    facade = state.get_or_create_facade()

    try:
        helper = getattr(facade, node.entity_type)
    except AttributeError:
        return HTMLResponse(status_code=400)

    # field_name reaches field_info when the cleared table is re-rendered
    # below, which raises KeyError -> 500 for a name the entity lacks.
    if field_name not in set(helper.all_fields):
        return HTMLResponse(status_code=400)

    # Get current parent values and clear the single entity field
    current_values: dict[str, Any] = {}
    if hasattr(node.instance, "model_dump"):
        current_values = node.instance.model_dump(exclude_none=True)

    # Remove the nested field
    if field_name in current_values:
        del current_values[field_name]

    # Create updated parent instance (skip validation)
    model_class = helper.model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state, user)

    # Return empty inline table HTML for the cleared field. field_name is a
    # caller-controlled path parameter, so escape the interpolations.
    field_info = helper.field_info(field_name)
    nested_type = escape(field_info.get("items", "Entity"))
    safe_field_name = escape(field_name)

    html = f"""<div class="inline-table-section" id="inline-table-{safe_field_name}">
    <div class="inline-table-header">
        <div class="inline-table-title">
            <span class="inline-table-icon">&#9660;</span>
            <h4>{escape(field_name.replace("_", " ").title())}</h4>
        </div>
    </div>
    <div class="inline-table-content">
        <p class="text-muted text-center" style="padding: 1rem;">
            No {nested_type} set. This field is optional.
        </p>
    </div>
</div>"""

    response = HTMLResponse(html)
    response.headers["HX-Trigger"] = "entityChanged"
    return response


@router.get("/datasets/{dataset_id}/lookup/{entity_type}")
async def lookup_entities(
    request: Request,
    dataset_id: str,
    entity_type: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: OptionalUser,
    q: str = "",
) -> Response:
    """Values of ``entity_type`` in this dataset, for a reference field.

    A reference names a row that has to exist: a Sample's study, an assay's
    material. Typed by hand it is the commonest way an import arrives with
    something attached to nothing, which is why the exported spreadsheet turns
    these columns into dropdowns. The tables in the application deserve the
    same, and this is what fills them.

    Scoped to one dataset, and to a user who may read it: values from someone
    else's dataset would be both useless and a disclosure.
    """
    from fastapi.responses import JSONResponse

    from metaseed_hub.ui.dependencies import AuthRequiredError, get_dataset_for_user
    from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade

    if user is None:
        # The dropdown is filled by fetch, so the refusal carries the sign-in
        # URL rather than an empty result set that reads as "no matches".
        raise AuthRequiredError(as_json=True)

    dataset = await get_dataset_for_user(dataset_id, session, user)
    state = await ensure_dataset_facade(dataset, session)
    facade = state.get_or_create_facade()

    helper = getattr(facade, entity_type, None)
    if helper is None:
        return JSONResponse({"results": []})

    identifier = getattr(helper, "identifier_field", None)
    label_field = next(
        (
            candidate
            for candidate in ("title", "name", "description")
            if candidate in getattr(helper, "all_fields", [])
        ),
        None,
    )

    query = q.lower().strip()
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in state.nodes_by_id.values():
        if node.entity_type != entity_type:
            continue
        data = node.instance.model_dump() if hasattr(node.instance, "model_dump") else {}
        value = str(data.get(identifier, "") or "")
        if not value or value in seen:
            continue
        label = str(data.get(label_field, "") or "") if label_field else ""
        if query and query not in value.lower() and query not in label.lower():
            continue
        seen.add(value)
        results.append({"value": value, "label": label})

    return JSONResponse({"results": results[:50]})
