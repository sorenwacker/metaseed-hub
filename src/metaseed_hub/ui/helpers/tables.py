"""Entity form context and inline table rendering data for the editor UI."""

import logging
from typing import Any

from metaseed_hub.ui.metaseed_ui import AppState, TreeNode

logger = logging.getLogger("metaseed_hub")


def parent_reference_field(parent_entity_type: str) -> str:
    """Name the column on a child row that references the given parent type.

    The table renderer greys this column out and the block-apply route refuses
    to write it; both must mean the same column, so the rule lives here once.
    """
    return f"{parent_entity_type.lower()}_id"


def build_entity_form_context(
    state: AppState,
    helper: Any,
    node_id: str | None = None,
    parent_id: str | None = None,
) -> dict[str, Any]:
    """Build form context for entity editing.

    Args:
        state: Application state containing nodes.
        helper: Entity helper from facade.
        node_id: ID of the entity being edited (None for new entities).
        parent_id: ID of the parent entity (for nested entities).

    Returns:
        Dictionary with form context including fields and values.
    """
    # Build field data for template
    fields = []
    for field_name in helper.all_fields:
        info = helper.field_info(field_name)
        field_type = info.get("type", "string")
        is_nested = field_type in ("list", "entity") and info.get("items") is not None
        # Get ontologies list for OLS lookup filtering
        ontologies = info.get("ontologies")  # list[str] | None

        fields.append(
            {
                "name": field_name,
                "type": field_type,
                "required": info.get("required", False),
                "description": info.get("description", ""),
                "constraints": info.get("constraints"),
                "item_type": info.get("items"),
                "is_nested": is_nested,
                "is_single_entity": field_type == "entity" and info.get("items") is not None,
                "ontologies": ontologies,
            }
        )

    # Determine inherited field to hide (e.g., investigation_id when parent is Investigation)
    inherited_field = None
    if parent_id and parent_id in state.nodes_by_id:
        parent_node = state.nodes_by_id[parent_id]
        inherited_field = f"{parent_node.entity_type.lower()}_id"

    # Separate required, optional, and nested fields (excluding inherited field)
    required_fields = [
        f for f in fields if f["required"] and not f["is_nested"] and f["name"] != inherited_field
    ]
    optional_fields = [
        f
        for f in fields
        if not f["required"] and not f["is_nested"] and f["name"] != inherited_field
    ]
    nested_fields = [f for f in fields if f["is_nested"]]

    # Get current values if editing existing entity
    values: dict[str, object] = {}
    if node_id and node_id in state.nodes_by_id:
        node = state.nodes_by_id[node_id]
        if hasattr(node.instance, "model_dump"):
            values = node.instance.model_dump(exclude_none=True)

    # Build inline tables for nested fields
    inline_tables = {}
    if node_id:
        inline_tables = build_inline_tables(state, node_id, nested_fields)

    return {
        "description": helper.description,
        "required_fields": required_fields,
        "optional_fields": optional_fields,
        "nested_fields": nested_fields,
        "inline_tables": inline_tables,
        "values": values,
    }


def _get_columns_from_helper(
    helper: Any,
) -> tuple[list[str], dict[str, str], set[str]]:
    """Extract column info from an entity helper.

    Extracts non-nested field information from a helper, returning the column
    names, their types, and which columns are required.

    Args:
        helper: Entity helper from facade.

    Returns:
        Tuple of (columns, column_types, required_columns) where:
            - columns: List of field names for non-nested fields.
            - column_types: Dict mapping field name to type string.
            - required_columns: Set of field names that are required.
    """
    columns = []
    column_types = {}
    required_columns = set()
    for fname in helper.all_fields:
        info = helper.field_info(fname)
        is_nested = info.get("type") in ("list", "entity") and info.get("items")
        if not is_nested:
            columns.append(fname)
            column_types[fname] = info.get("type", "string")
            if info.get("required"):
                required_columns.add(fname)
    return columns, column_types, required_columns


def _build_primitive_list_table(
    node: TreeNode,
    field_name: str,
    item_type: str,
) -> dict[str, Any]:
    """Build table data for a primitive list field.

    Handles fields that contain lists of primitive types like strings,
    integers, floats, etc.

    Args:
        node: TreeNode containing the parent instance.
        field_name: Name of the field containing the primitive list.
        item_type: Type of the primitive items (e.g., "string", "integer").

    Returns:
        Dictionary with table data including columns, rows, and type info.
    """
    rows = []
    if hasattr(node.instance, "model_dump"):
        parent_data = node.instance.model_dump(exclude_none=True)
        current_list = parent_data.get(field_name, []) or []
        for idx, val in enumerate(current_list):
            rows.append({"_idx": idx, "value": val})

    return {
        "columns": ["value"],
        "rows": rows,
        "column_types": {"value": item_type.lower()},
        "nested_entity_type": item_type,
        "is_primitive_list": True,
    }


def _build_single_entity_table(
    node: TreeNode,
    field_name: str,
    item_type: str,
    helper: Any,
) -> dict[str, Any]:
    """Build table data for a single entity field.

    Handles fields of type 'entity' that contain a single nested entity
    rather than a list of entities.

    Args:
        node: TreeNode containing the parent instance.
        field_name: Name of the field containing the entity.
        item_type: Type name of the nested entity.
        helper: Entity helper for the nested type.

    Returns:
        Dictionary with table data including columns, rows, and type info.
    """
    # Get entity data from parent instance
    entity_data: dict[str, Any] = {}
    if hasattr(node.instance, "model_dump"):
        parent_data = node.instance.model_dump(exclude_none=True)
        entity_data = parent_data.get(field_name, {}) or {}

    # Build columns from helper's simple fields
    columns, column_types, required_columns = _get_columns_from_helper(helper)

    # Always create one row for single entities (editable even if empty)
    row_data: dict[str, Any] = {"_idx": 0}
    if entity_data and isinstance(entity_data, dict):
        for col in columns:
            row_data[col] = entity_data.get(col, "")
    else:
        for col in columns:
            row_data[col] = ""
    rows = [row_data]

    return {
        "columns": columns,
        "rows": rows,
        "column_types": column_types,
        "nested_entity_type": item_type,
        "required_columns": list(required_columns),
        "reference_fields": _reference_fields(helper),
        "is_single_entity": True,
    }


def _build_entity_list_table(
    node: TreeNode,
    field_name: str,
    item_type: str,
    helper: Any,
) -> dict[str, Any]:
    """Build table data for a list of entities field.

    Handles fields that contain lists of nested entity types, building
    rows from TreeNode children.

    Args:
        node: TreeNode containing the parent instance and children.
        field_name: Name of the field containing the entity list.
        item_type: Type name of the nested entities.
        helper: Entity helper for the nested type.

    Returns:
        Dictionary with table data including columns, rows, and type info.
    """
    # Find children of this type under this node
    children = [child for child in node.children if child.entity_type == item_type]

    # Build columns from helper's simple fields (exclude nested)
    columns, column_types, required_columns = _get_columns_from_helper(helper)
    display_columns = columns

    # Build rows from children
    rows: list[dict[str, Any]] = []
    for idx, child in enumerate(children):
        row_data: dict[str, Any] = {"_idx": idx, "_node_id": child.id}
        if hasattr(child.instance, "model_dump"):
            data = child.instance.model_dump(exclude_none=True)
            for col in display_columns:
                # The full value, always: these rows feed editable inputs whose
                # blur handler saves whatever they hold, so a display-truncated
                # value ("...") would be written back over the stored one.
                # Truncation is the template's job (CSS text-overflow).
                row_data[col] = data.get(col, "")
        rows.append(row_data)

    # If no children found, check if instance has this field as primitive data
    # This handles cases where schema expects entities but data has plain values
    if not rows and hasattr(node.instance, "model_dump"):
        parent_data = node.instance.model_dump(exclude_none=True)
        field_data = parent_data.get(field_name)

        if field_data is not None:
            # Handle list of primitives
            if isinstance(field_data, list) and field_data:
                first_item = field_data[0]
                if not isinstance(first_item, dict):
                    for idx, val in enumerate(field_data):
                        rows.append({"_idx": idx, "value": str(val)})
                    return {
                        "columns": ["value"],
                        "rows": rows,
                        "column_types": {"value": "string"},
                        "nested_entity_type": item_type,
                        "is_primitive_list": True,
                    }
            # Handle single primitive value (for type=entity fields)
            elif not isinstance(field_data, dict | list):
                rows.append({"_idx": 0, "value": str(field_data)})
                return {
                    "columns": ["value"],
                    "rows": rows,
                    "column_types": {"value": "string"},
                    "nested_entity_type": item_type,
                    "is_primitive_list": True,
                }

    # Determine which columns are inherited (reference to parent)
    inherited = parent_reference_field(node.entity_type)
    inherited_columns = {col for col in display_columns if col == inherited}

    return {
        "columns": display_columns,
        "rows": rows,
        "column_types": column_types,
        "nested_entity_type": item_type,
        "inherited_columns": list(inherited_columns),
        "required_columns": list(required_columns),
        "reference_fields": _reference_fields(helper),
    }


def _reference_fields(helper: Any) -> dict[str, dict[str, str]]:
    """Column -> the entity and field it names, for the columns that name one.

    A reference typed by hand is how rows end up attached to nothing. The
    exported spreadsheet turns these into dropdowns; the tables here get the
    same lookup, fed by the dataset-scoped route in ui/routes/table.py.
    """
    return {
        field: {"target_entity": target_entity, "target_field": target_field}
        for field, (target_entity, target_field) in (
            getattr(helper, "reference_fields", {}) or {}
        ).items()
    }


def build_inline_tables(
    state: AppState,
    node_id: str,
    nested_fields: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build inline table data for nested fields of a node.

    Dispatches to specialized helper functions based on field type:
    - Primitive lists (strings, integers, etc.)
    - Single entity fields
    - Entity list fields

    Args:
        state: Application state containing nodes.
        node_id: ID of the parent node.
        nested_fields: List of nested field definitions.

    Returns:
        Dictionary mapping field names to table data.
    """
    if node_id not in state.nodes_by_id:
        return {}

    node = state.nodes_by_id[node_id]
    facade = state.get_or_create_facade()
    inline_tables: dict[str, dict[str, Any]] = {}

    # Primitive types that are not entities
    primitive_types = {"string", "integer", "float", "boolean", "date", "datetime", "uri"}

    for field in nested_fields:
        field_name = field["name"]
        item_type = field.get("item_type")

        # Handle missing item type
        if not item_type:
            inline_tables[field_name] = {
                "columns": [],
                "rows": [],
                "column_types": {},
                "nested_entity_type": "Unknown",
            }
            continue

        # Handle primitive list types (e.g., list of strings)
        if item_type.lower() in primitive_types:
            inline_tables[field_name] = _build_primitive_list_table(node, field_name, item_type)
            continue

        # Get helper for the nested entity type
        try:
            helper = getattr(facade, item_type)
        except AttributeError:
            inline_tables[field_name] = {
                "columns": [],
                "rows": [],
                "column_types": {},
                "nested_entity_type": item_type,
                "error": f"Unknown entity type: {item_type}",
            }
            continue

        # Determine if this is a single entity field
        is_single_entity = field.get("is_single_entity", False)
        if not is_single_entity and hasattr(node.instance, "model_dump"):
            # Check if the actual data is a dict (single entity) not a list
            parent_data = node.instance.model_dump(exclude_none=True)
            field_data = parent_data.get(field_name)
            if isinstance(field_data, dict) and field_data:
                is_single_entity = True
                logger.debug(f"Detected single entity from data for field {field_name}")

        # Dispatch to appropriate handler
        if is_single_entity:
            inline_tables[field_name] = _build_single_entity_table(
                node, field_name, item_type, helper
            )
        else:
            inline_tables[field_name] = _build_entity_list_table(
                node, field_name, item_type, helper
            )

    return inline_tables
