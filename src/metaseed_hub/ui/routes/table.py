"""Inline table routes for Hub UI.

Handles CRUD operations for inline entity tables and primitive lists.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from metaseed.ui.state import AppState, TreeNode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.database import get_session
from metaseed_hub.models import Project
from metaseed_hub.ui.dependencies import (
    get_current_user_from_cookie,
    unauthorized_response,
)
from metaseed_hub.ui.helpers import (
    deserialize_tree,
    save_project_state,
    validate_csrf_token,
)

router = APIRouter(tags=["table"])

# Primitive types that are not entity types
PRIMITIVE_TYPES = {"string", "integer", "float", "boolean", "date", "datetime", "uri"}


def _get_project_state(project: Project) -> AppState:
    """Get or create AppState for a project, loading from database.

    Args:
        project: Project model with profile, version, and data fields.

    Returns:
        AppState populated with project's entity tree.
    """
    state = AppState()
    state.profile = project.profile
    state.version = project.version
    if project.data:
        deserialize_tree(state, project.data)
    return state


def _handle_primitive_list_row(
    project_id: str,
    parent_node: TreeNode,
    field_name: str,
    nested_type: str,
    state: AppState,
) -> tuple[int, list[Any]]:
    """Handle adding a new row to a primitive list.

    Args:
        project_id: ID of the project.
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
    project_id: str,
    parent_node_id: str,
    field_name: str,
    row_idx: int,
    nested_type: str,
) -> str:
    """Build HTML for a new primitive list row.

    Args:
        project_id: ID of the project.
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

    return default_values


def _build_entity_row_html(
    project_id: str,
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
        project_id: ID of the project.
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

    return html


@router.post(
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
    """Add a new row to an inline table.

    Handles both primitive lists (strings, integers, etc.) and entity lists.
    For primitive lists, adds a new empty value to the list.
    For entity lists, creates a new child entity with default values.
    """
    user = await get_current_user_from_cookie(request)
    if not user:
        return unauthorized_response()

    if not validate_csrf_token(request):
        return HTMLResponse("<tr><td>CSRF validation failed</td></tr>", status_code=403)

    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse("<tr><td>Project not found</td></tr>")

    state = _get_project_state(project)

    if parent_node_id not in state.nodes_by_id:
        return HTMLResponse("<tr><td>Parent entity not found</td></tr>")

    parent_node = state.nodes_by_id[parent_node_id]
    facade = state.get_or_create_facade()

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
                project_id, parent_node, field_name, nested_type, state
            )

            # Update parent instance with new list
            update_data = parent_node.instance.model_dump(exclude_none=True)
            update_data[field_name] = current_list
            updated_instance = parent_helper.create(**update_data)
            state.update_node(parent_node_id, updated_instance)

            # Save to database
            await save_project_state(session, project, state)

            # Return row HTML for primitive value
            html = _build_primitive_row_html(
                project_id, parent_node_id, field_name, row_idx, nested_type
            )

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
    default_values = _get_default_values(nested_helper, parent_node, parent_identifier)

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
        project_id,
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


@router.post("/projects/{project_id}/table/{node_id}/cell")
async def update_table_cell(
    request: Request,
    project_id: str,
    node_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Update a single cell value in an inline table."""
    user = await get_current_user_from_cookie(request)
    if not user:
        return unauthorized_response()

    if not validate_csrf_token(request):
        return HTMLResponse(status_code=403)

    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse(status_code=404)

    state = _get_project_state(project)

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

        if field_type == "integer":
            current_values[field_name] = int(raw_str)
        elif field_type == "float":
            current_values[field_name] = float(raw_str)
        elif field_type == "boolean":
            current_values[field_name] = raw_str.lower() == "true"
        else:
            current_values[field_name] = raw_str

    instance = helper.create(**current_values)
    state.update_node(node_id, instance)
    await save_project_state(session, project, state)

    return Response(
        status_code=200,
        headers={"HX-Trigger": "entityChanged"},
    )


@router.post(
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
        return unauthorized_response()

    if not validate_csrf_token(request):
        return HTMLResponse(status_code=403)

    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse(status_code=404)

    state = _get_project_state(project)

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


@router.delete(
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
        return unauthorized_response()

    if not validate_csrf_token(request):
        return HTMLResponse(status_code=403)

    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        return HTMLResponse(status_code=404)

    state = _get_project_state(project)

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
