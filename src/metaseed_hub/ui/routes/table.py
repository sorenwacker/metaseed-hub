"""Inline table routes for Hub UI.

Handles CRUD operations for inline entity tables and primitive lists.
"""

import uuid
from html import escape
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from metaseed.ui.state import AppState, TreeNode
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.database import get_session
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import get_dataset_state_for_mutation
from metaseed_hub.ui.forms import parse_form_field
from metaseed_hub.ui.helpers import save_dataset_state

router = APIRouter(tags=["table"])

# Primitive types that are not entity types
PRIMITIVE_TYPES = {"string", "integer", "float", "boolean", "date", "datetime", "uri"}


def _handle_primitive_list_row(
    dataset_id: str,
    parent_node: TreeNode,
    field_name: str,
    nested_type: str,
    state: AppState,
) -> tuple[int, list[Any]]:
    """Handle adding a new row to a primitive list.

    Args:
        dataset_id: ID of the dataset.
        parent_node: Parent TreeNode containing the list.
        field_name: Name of the list field.
        nested_type: Type of items in the list.
        state: Application state.

    Returns:
        Tuple of (row_idx, updated_list).
    """
    # Get current list from parent instance
    current_list: list[Any] = []
    if hasattr(parent_node.instance, "model_dump"):
        parent_data = parent_node.instance.model_dump(exclude_none=True)
        current_list = parent_data.get(field_name, []) or []

    # Add new empty value
    row_idx = len(current_list)
    current_list.append("")

    return row_idx, current_list


def _build_primitive_row_html(
    dataset_id: str,
    parent_node_id: str,
    field_name: str,
    row_idx: int,
    nested_type: str,
) -> str:
    """Build HTML for a new primitive list row.

    Args:
        dataset_id: ID of the dataset.
        parent_node_id: ID of the parent node.
        field_name: Name of the list field.
        row_idx: Index of the new row.
        nested_type: Type of items in the list.

    Returns:
        HTML string for the table row.
    """
    input_type = "text"
    if nested_type.lower() in ("integer", "float"):
        input_type = "number"
    elif nested_type.lower() == "date":
        input_type = "date"
    elif nested_type.lower() == "datetime":
        input_type = "datetime-local"

    post_url = f"/hub/datasets/{dataset_id}/table/{parent_node_id}"
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

    return html


def _get_default_values(
    nested_helper: Any,
    parent_node: TreeNode,
    parent_identifier: Any,
) -> dict[str, str | int | float | bool]:
    """Generate default values for required fields of a nested entity.

    Args:
        nested_helper: Helper for the nested entity type.
        parent_node: Parent TreeNode.
        parent_identifier: Identifier value from parent entity.

    Returns:
        Dictionary of field names to default values.
    """
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
            # Check if this references the parent type - only auto-fill if it does
            ref_type = fname[:-3]  # Remove "_id" suffix
            if ref_type == parent_type_lower and parent_identifier:
                default_values[fname] = str(parent_identifier)
            # Otherwise leave empty for user to fill in
        elif field_type == "integer":
            default_values[fname] = 0
        elif field_type == "float":
            default_values[fname] = 0.0
        elif field_type == "boolean":
            default_values[fname] = False
        else:
            # String or other - use field name as placeholder
            default_values[fname] = f"New {fname.replace('_', ' ').title()}"

    return default_values


def _build_entity_row_html(
    dataset_id: str,
    field_name: str,
    row_idx: int,
    child_node: TreeNode,
    nested_type: str,
    columns: list[str],
    column_types: dict[str, str],
    inherited_cols: set[str],
    instance_data: dict[str, Any],
) -> str:
    """Build HTML for a new entity table row.

    Args:
        dataset_id: ID of the dataset.
        field_name: Name of the nested field.
        row_idx: Index of the new row.
        child_node: TreeNode for the new child entity.
        nested_type: Entity type name.
        columns: List of column names.
        column_types: Map of column names to types.
        inherited_cols: Set of columns inherited from parent.
        instance_data: Data from the entity instance.

    Returns:
        HTML string for the table row.
    """
    node_id = child_node.id

    html = f'<tr id="row-{field_name}-{row_idx}" data-idx="{row_idx}" '
    html += f'data-node-id="{node_id}">'

    for col in columns:
        col_type = column_types.get(col, "string")
        cell_value = instance_data.get(col, "")
        safe_cell_value = escape(str(cell_value)) if cell_value != "" else ""

        # Inherited columns are read-only
        if col in inherited_cols:
            html += f"""<td class="readonly-cell" data-col="{col}">
                <span class="cell-display inherited">{safe_cell_value}</span>
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
            display_value = safe_cell_value or "Click to edit"
            placeholder_class = " placeholder" if not cell_value else ""
            post_url = f"/hub/datasets/{dataset_id}/table/{child_node.id}/cell"
            html += f"""<td class="editable-cell" data-col="{col}">
                <span class="cell-display{placeholder_class}">{display_value}</span>
                <input type="{input_type}" class="cell-input" name="{col}"
                       value="{safe_cell_value}" {step}
                       hx-post="{post_url}" hx-trigger="change, blur" hx-swap="none">
            </td>"""

    html += f"""<td class="row-actions">
        <button type="button" class="btn-icon"
                hx-get="/hub/datasets/{dataset_id}/entity/{child_node.id}"
                hx-target="#editor"
                hx-swap="innerHTML"
                title="Edit">&#9998;</button>
        <button type="button" class="btn-icon danger"
                hx-delete="/hub/datasets/{dataset_id}/entity/{child_node.id}"
                hx-target="#row-{field_name}-{row_idx}"
                hx-swap="delete"
                hx-confirm="Delete this {nested_type}?"
                title="Delete">&#128465;</button>
    </td></tr>"""

    return html


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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Add a new row to an inline table.

    Handles both primitive lists (strings, integers, etc.) and entity lists.
    For primitive lists, adds a new empty value to the list.
    For entity lists, creates a new child entity with default values.
    """
    import logging

    logger = logging.getLogger("metaseed_hub")
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
        field_info = parent_helper.field_info(field_name)
        nested_type = field_info.get("items")
        if not nested_type:
            return HTMLResponse("<tr><td>Invalid field</td></tr>")

        # Handle primitive list types (list of strings, integers, etc.)
        if nested_type.lower() in PRIMITIVE_TYPES:
            row_idx, current_list = _handle_primitive_list_row(
                dataset_id, parent_node, field_name, nested_type, state
            )

            # Update parent instance with new list (use model_construct to skip validation)
            update_data = parent_node.instance.model_dump(exclude_none=True)
            update_data[field_name] = current_list
            model_class = parent_helper._model
            updated_instance = model_class.model_construct(**update_data)
            state.update_node(parent_node_id, updated_instance)

            # Save to database
            await save_dataset_state(session, dataset, state)

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
        session, dataset, state, dataset_id, parent_node_id, field_name, nested_type, nested_helper
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
    model_class = nested_helper._model
    instance = model_class.model_construct(**default_values)

    # Add as child of parent (create TreeNode directly to skip facade validation)
    node_id = str(uuid.uuid4())
    label = nested_helper.get_label(instance) or f"New {nested_type}"
    child_node = TreeNode(
        id=node_id,
        entity_type=nested_type,
        instance=instance,
        label=label,
        parent_id=parent_node_id,
    )
    parent_node.children.append(child_node)
    state.nodes_by_id[node_id] = child_node

    # Save to database
    await save_dataset_state(session, dataset, state)

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
    for field_name, raw_value in form_data.items():
        if field_name.startswith("_"):
            continue
        if raw_value is None or raw_value == "":
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

    model_class = helper._model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)
    await save_dataset_state(session, dataset, state)

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

    current_list = current_values.get(field_name, []) or []
    if idx < len(current_list):
        current_list[idx] = new_value
    current_values[field_name] = current_list

    # Create updated instance (skip validation)
    model_class = helper._model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state)

    return HTMLResponse(status_code=200, headers={"HX-Trigger": "entityChanged"})


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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Delete a primitive list item."""
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

    current_list = current_values.get(field_name, []) or []
    if idx < len(current_list):
        current_list.pop(idx)
    current_values[field_name] = current_list

    # Create updated instance (skip validation)
    model_class = helper._model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state)

    response = HTMLResponse(status_code=200)
    response.headers["HX-Trigger"] = "entityChanged"
    return response


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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Update a single entity field (e.g., measurement_type, technology_type).

    Single entity fields are nested objects that are not lists - they contain
    a single instance of another entity type embedded in the parent.
    """
    import logging

    logger = logging.getLogger("metaseed_hub")
    logger.info(f"update_single_entity_field: node_id={node_id}, field_name={field_name}")

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

    logger.info(f"update_single_entity_field: nested_data={nested_data}")
    logger.info(
        f"update_single_entity_field: current_values BEFORE={current_values.get(field_name)}"
    )

    # Merge new values with existing nested field data (don't replace entirely)
    existing_nested = current_values.get(field_name, {}) or {}
    if isinstance(existing_nested, dict):
        existing_nested.update(nested_data)
        current_values[field_name] = existing_nested
    else:
        current_values[field_name] = nested_data

    logger.info(
        f"update_single_entity_field: current_values AFTER={current_values.get(field_name)}"
    )

    # Create updated parent instance (skip validation)
    model_class = helper._model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state)
    logger.info("update_single_entity_field: saved successfully")

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

    # Get current parent values and clear the single entity field
    current_values: dict[str, Any] = {}
    if hasattr(node.instance, "model_dump"):
        current_values = node.instance.model_dump(exclude_none=True)

    # Remove the nested field
    if field_name in current_values:
        del current_values[field_name]

    # Create updated parent instance (skip validation)
    model_class = helper._model
    instance = model_class.model_construct(**current_values)
    state.update_node(node_id, instance)

    # Save to database
    await save_dataset_state(session, dataset, state)

    # Return empty inline table HTML for the cleared field
    field_info = helper.field_info(field_name)
    nested_type = field_info.get("items", "Entity")

    html = f"""<div class="inline-table-section" id="inline-table-{field_name}">
    <div class="inline-table-header">
        <div class="inline-table-title">
            <span class="inline-table-icon">&#9660;</span>
            <h4>{field_name.replace('_', ' ').title()}</h4>
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
