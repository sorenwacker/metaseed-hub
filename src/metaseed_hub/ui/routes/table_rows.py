"""Row fragments for the inline tables.

Building a row's HTML and deciding whether a request addresses a real item are
not routing concerns; separating them keeps `table.py` inside the file-size
gate and gives the row builders a home their tests can import directly.
"""

import uuid
from html import escape
from typing import Any

from metaseed_hub.ui.metaseed_ui import TreeNode

# Primitive types that are not entity types
PRIMITIVE_TYPES = {"string", "integer", "float", "boolean", "date", "datetime", "uri"}


def _addresses_an_item(values: object, idx: int) -> bool:
    """Whether `idx` names an existing item of `values`.

    Python's negative indexing makes `idx < len(values)` true for every
    negative number, so a request naming -1 would edit the last item instead
    of being refused. An index past the end addresses nothing either.

    Args:
        values: The stored field value, which need not be a list.
        idx: The index taken from the request path.

    Returns:
        True when `values` is a list and `idx` names one of its items.
    """
    return isinstance(values, list) and 0 <= idx < len(values)


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
