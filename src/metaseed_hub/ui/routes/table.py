"""Inline table routes for Hub UI.

Handles CRUD operations for inline entity tables and primitive lists.
"""

import logging
import uuid
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
from metaseed_hub.ui.helpers.tables import parent_reference_field
from metaseed_hub.ui.metaseed_ui import AppState, TreeNode
from metaseed_hub.ui.render import render_template

router = APIRouter(tags=["table"])

logger = logging.getLogger("metaseed_hub")

# Primitive types that are not entity types
PRIMITIVE_TYPES = {"string", "integer", "float", "boolean", "date", "datetime", "uri"}

# A block apply rebuilds one entity per row it touches; a selection is bounded
# by the visible table, so anything far larger is a crafted request.
MAX_BLOCK_CELLS = 1000


def _handle_primitive_list_row(
    parent_node: TreeNode,
    field_name: str,
) -> tuple[int, list[Any]]:
    """Handle adding a new row to a primitive list.

    Args:
        parent_node: Parent TreeNode containing the list.
        field_name: Name of the list field.

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

    # field_name is a caller-controlled path parameter interpolated into
    # attribute values; escape it so it cannot break out of the attribute.
    field_name = escape(field_name)

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

        # Only what is genuinely derivable is pre-filled: a fresh identifier
        # (the row needs an identity to be addressable) and the reference to
        # the parent it was created under. Fabricated placeholders ("New
        # Title", 0, False) persisted as real values with validation skipped
        # — data the user never entered, saved as if they had.
        if fname in ("unique_id", "id", "identifier"):
            default_values[fname] = str(uuid.uuid4())[:8]
        elif fname.endswith("_id"):
            ref_type = fname[:-3]  # Remove "_id" suffix
            if ref_type == parent_type_lower and parent_identifier:
                default_values[fname] = str(parent_identifier)

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

    # Structural values interpolated into attributes must not break out of the
    # attribute context; field_name in particular is caller-controlled.
    field_name = escape(field_name)
    nested_type = escape(nested_type)

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


class BlockApplyRefusedError(Exception):
    """A block apply that must not be written, with the reason to report."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _resolved_cell(state: AppState, cell: Any) -> tuple[str, str, str, Any]:
    """Check one cell of a block and return what writing it needs.

    Every cell is resolved before any is written, so a block that cannot be
    applied whole is refused whole.

    Raises:
        BlockApplyRefusedError: The cell names a missing row, a field the entity does
            not have, or the column that references the row's parent.
    """
    if not isinstance(cell, dict):
        raise BlockApplyRefusedError("A selected cell is malformed.")

    node_id = cell.get("node_id")
    field_name = cell.get("field")
    raw_value = cell.get("value", "")
    if (
        not isinstance(node_id, str)
        or not isinstance(field_name, str)
        or not isinstance(raw_value, str)
    ):
        raise BlockApplyRefusedError("A selected cell is malformed.")

    node = state.nodes_by_id.get(node_id)
    if node is None:
        raise BlockApplyRefusedError(
            "A selected row no longer exists. Reload the dataset and select again."
        )

    facade = state.get_or_create_facade()
    try:
        helper = getattr(facade, node.entity_type)
    except AttributeError as exc:
        raise BlockApplyRefusedError(f"{node.entity_type} is not part of this profile.") from exc

    if field_name not in set(helper.all_fields):
        raise BlockApplyRefusedError(f"{field_name} is not a field of {node.entity_type}.")

    parent = state.nodes_by_id.get(node.parent_id) if node.parent_id else None
    if parent is not None and field_name == parent_reference_field(parent.entity_type):
        raise BlockApplyRefusedError(
            f"{field_name} links a row to its {parent.entity_type}; "
            "change it by editing the row, not by filling cells."
        )

    return node_id, field_name, raw_value, helper


@router.post("/datasets/{dataset_id}/table/cells")
async def apply_value_to_cells(
    request: Request,
    dataset_id: str,
    dataset_state: Annotated[tuple[Dataset, AppState], Depends(get_dataset_state_for_mutation)],
    user: OptionalUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Write a block of cells, each with its own value, as a single save.

    Filling one value into a selection sends that value repeated; pasting sends
    values that differ. Both are this one write, so the two gestures cannot
    drift apart on what is allowed or how a value is converted.
    """
    dataset, state = dataset_state

    try:
        payload = await request.json()
    except Exception:
        return HTMLResponse("Malformed request.", status_code=400)

    if not isinstance(payload, dict):
        return HTMLResponse("Malformed request.", status_code=400)
    cells = payload.get("cells")
    if not isinstance(cells, list) or not cells:
        return HTMLResponse("Nothing to apply.", status_code=400)
    if len(cells) > MAX_BLOCK_CELLS:
        # A selection is bounded by what is on screen; an unbounded batch is a
        # crafted request, and rebuilding that many rows blocks the worker.
        return HTMLResponse(f"A block is limited to {MAX_BLOCK_CELLS} cells.", status_code=400)

    try:
        resolved = [_resolved_cell(state, cell) for cell in cells]
    except BlockApplyRefusedError as refusal:
        return HTMLResponse(escape(refusal.reason), status_code=400)

    # Resolution is complete, so every write below lands or the batch was
    # already refused: group by row, since several cells of one row share an
    # instance and must be rebuilt together.
    updates: dict[str, dict[str, Any]] = {}
    for node_id, field_name, raw_value, helper in resolved:
        values = updates.get(node_id)
        if values is None:
            instance = state.nodes_by_id[node_id].instance
            values = (
                instance.model_dump(exclude_none=True) if hasattr(instance, "model_dump") else {}
            )
            updates[node_id] = values

        if raw_value == "":
            values.pop(field_name, None)
            continue
        field_type = helper.field_info(field_name).get("type", "string")
        try:
            values[field_name] = parse_form_field(raw_value, field_type)
        except ValueError:
            # A value the column cannot hold is kept as typed rather than
            # dropped: validation reports it, silence would lose it.
            values[field_name] = raw_value

    for node_id, values in updates.items():
        node = state.nodes_by_id[node_id]
        helper = getattr(state.get_or_create_facade(), node.entity_type)
        # Written as a draft, like an added row: a value the column cannot hold
        # is reported by validation, not lost between the paste and the save.
        state.update_node(node_id, helper.model.model_construct(**values), skip_validation=True)

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

    current_list = current_values.get(field_name, []) or []
    if idx < len(current_list):
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

    current_list = current_values.get(field_name, []) or []
    if idx < len(current_list):
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

    from metaseed_hub.ui.dependencies import get_dataset_for_user
    from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade

    if user is None:
        return JSONResponse({"results": []}, status_code=401)

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
