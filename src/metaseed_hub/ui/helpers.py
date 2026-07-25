"""Shared helper functions for Hub UI routes."""

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException, Request, UploadFile
from metaseed.ui.state import AppState, TreeNode

from metaseed_hub.config import get_settings
from metaseed_hub.models import Dataset, DatasetVersion
from metaseed_hub.ui.forms import get_label_from_values

logger = logging.getLogger("metaseed_hub")


def make_json_serializable(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects to serializable types.

    Handles: date, datetime, Pydantic URLs (AnyUrl, HttpUrl), and other objects
    by converting them to strings.

    Use this before storing data in JSONB columns to avoid serialization errors.

    Args:
        obj: Any object that may contain non-serializable values.

    Returns:
        Object with all non-serializable types converted to JSON-compatible types.
    """
    # Handle None and basic JSON types first
    if obj is None or isinstance(obj, bool | int | float | str):
        return obj
    # Handle datetime before date (datetime is subclass of date)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    # Handle collections
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [make_json_serializable(item) for item in obj]
    # Handle Pydantic URL types and any other object with string representation
    # This catches AnyUrl, HttpUrl, and similar Pydantic types
    return str(obj)


def add_entity_node(
    state: AppState,
    entity_type: str,
    data: dict[str, Any],
    parent_id: str | None = None,
    helper: Any = None,
) -> TreeNode:
    """Add an entity node without validation.

    Creates a TreeNode directly using model_construct to skip Pydantic validation.
    This allows saving entities with missing required fields.

    Args:
        state: AppState to add the node to.
        entity_type: Type of entity (e.g., "Investigation", "Study").
        data: Field values for the entity.
        parent_id: Optional parent node ID for hierarchy.
        helper: Optional entity helper. If not provided, will be fetched from facade.

    Returns:
        Created TreeNode.
    """
    facade = state.get_or_create_facade()
    if helper is None:
        helper = getattr(facade, entity_type)

    # Create instance using model_construct (skips validation)
    model_class = helper._model
    instance = model_class.model_construct(**data)

    # Generate node ID and label
    node_id = str(uuid.uuid4())
    label = helper.get_label(instance) or f"New {entity_type}"

    # Create TreeNode directly
    node = TreeNode(
        id=node_id,
        entity_type=entity_type,
        instance=instance,
        label=label,
        parent_id=parent_id,
    )

    # Add to state's tree structure
    if parent_id and parent_id in state.nodes_by_id:
        parent_node = state.nodes_by_id[parent_id]
        parent_node.children.append(node)
    else:
        state.entity_tree.append(node)

    state.nodes_by_id[node_id] = node

    return node


CSRF_TOKEN_COOKIE = "metaseed_csrf_token"

# Upper bound on an uploaded import file, enforced in the app rather than relying
# on the reverse proxy alone. A bare ``await file.read()`` loads the whole upload
# into memory (then parses it), so an unbounded read is a memory-pressure DoS on a
# deployment without an edge cap.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB


async def read_upload_capped(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an uploaded file fully, but refuse anything larger than ``max_bytes``.

    Args:
        file: The uploaded file.
        max_bytes: Maximum number of bytes to accept.

    Returns:
        The file content.

    Raises:
        HTTPException: 413 if the upload exceeds ``max_bytes``.
    """
    # Read one byte past the limit to distinguish "exactly at limit" from "over".
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large; the limit is {max_bytes // (1024 * 1024)} MiB.",
        )
    return content


def _sign_csrf(token: str) -> str:
    """Return the token with an HMAC signature keyed by the application secret.

    Signing lets the server recognise tokens it issued, so an attacker cannot
    fixate or forge the CSRF cookie without knowing ``secret_key``.

    Args:
        token: The random CSRF token to sign.

    Returns:
        The value ``"<token>.<hex-signature>"`` stored in the cookie and form.
    """
    secret = get_settings().secret_key.encode()
    signature = hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{signature}"


def _csrf_signature_valid(signed: str) -> bool:
    """Return True if a signed CSRF value carries a valid signature.

    Args:
        signed: A ``"<token>.<signature>"`` value from a cookie or form.

    Returns:
        True when the signature matches the application secret.
    """
    token, _, signature = signed.rpartition(".")
    if not token or not signature:
        return False
    secret = get_settings().secret_key.encode()
    expected = hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_or_create_csrf_token(request: Request) -> str:
    """Return the request's signed CSRF token, issuing a new one if needed.

    Args:
        request: The request object.

    Returns:
        A signed CSRF token to embed in the page and set as a cookie.
    """
    token = request.cookies.get(CSRF_TOKEN_COOKIE)
    if token and _csrf_signature_valid(token):
        return token
    return _sign_csrf(secrets.token_urlsafe(32))


def validate_csrf_token(request: Request, form_token: str | None = None) -> bool:
    """Validate the submitted CSRF token against the signed cookie.

    The cookie value must carry a valid application signature and match the
    token submitted in the header or form (double-submit).

    Args:
        request: The request object.
        form_token: Optional CSRF token from form data.

    Returns:
        True if the token is present, signed, and matches; False otherwise.
    """
    cookie_token = request.cookies.get(CSRF_TOKEN_COOKIE)
    # Check header first (for AJAX requests), then form data
    token = request.headers.get("X-CSRF-Token") or form_token

    if not cookie_token or not token:
        return False

    if not _csrf_signature_valid(cookie_token):
        return False

    # Constant-time comparison to prevent timing attacks
    return secrets.compare_digest(cookie_token, token)


def serialize_tree(state: AppState) -> dict[str, Any]:
    """Serialize AppState entity tree to JSON-compatible dict.

    Args:
        state: AppState with entity tree to serialize.

    Returns:
        Dictionary that can be stored as JSONB in database.
    """

    def serialize_node(node: TreeNode) -> dict[str, Any]:
        node_data: dict[str, Any] = {
            "id": node.id,
            "entity_type": node.entity_type,
            "label": node.label,
            "parent_id": node.parent_id,
            "data": {},
        }
        if node.instance and hasattr(node.instance, "model_dump"):
            # Use mode="json" to ensure datetime objects are serialized to strings
            node_data["data"] = node.instance.model_dump(exclude_none=True, mode="json")
        if node.children:
            node_data["children"] = [serialize_node(c) for c in node.children]
        return node_data

    result = {
        "profile": state.profile,
        "version": state.version,
        "tree": [serialize_node(n) for n in state.entity_tree],
    }
    # Ensure all date/datetime objects are converted to strings for JSON storage
    serialized: dict[str, Any] = make_json_serializable(result)
    return serialized


def deserialize_tree(state: AppState, data: dict[str, Any]) -> None:
    """Deserialize JSON data into AppState entity tree.

    Args:
        state: AppState to populate.
        data: Dictionary loaded from database JSONB.
    """
    if not data or "tree" not in data:
        logger.debug(
            f"deserialize_tree: no tree in data, keys={list(data.keys()) if data else 'None'}"
        )
        return

    facade = state.get_or_create_facade()
    if facade is None:
        logger.error("deserialize_tree: facade is None, cannot deserialize")
        return

    logger.debug(f"deserialize_tree: facade entities={facade.entities}")

    def deserialize_node(
        node_data: dict[str, Any],
        parent_id: str | None = None,
    ) -> TreeNode | None:
        entity_type = node_data.get("entity_type")
        if not entity_type:
            logger.warning("deserialize_node: no entity_type in node_data")
            return None

        try:
            helper = getattr(facade, entity_type)
        except AttributeError:
            logger.warning(f"deserialize_node: entity_type '{entity_type}' not in facade")
            return None

        # Create instance from stored data using skip_validation for permissive loading
        instance_data = node_data.get("data", {})
        instance = None
        try:
            instance = helper.create(skip_validation=True, **instance_data)
        except Exception as e:
            logger.warning(f"Failed to create {entity_type} with skip_validation: {e}")
            # Fall back to model_construct as last resort
            try:
                model_class = helper._model
                instance = model_class.model_construct(**instance_data)
                logger.info(f"Created {entity_type} with model_construct - data preserved")
            except Exception as e2:
                logger.error(f"model_construct also failed for {entity_type}: {e2}")
                instance = None

        if instance is None:
            logger.error(f"No instance created for {entity_type} - node will have no data")

        node = TreeNode(
            id=node_data.get("id", ""),
            entity_type=entity_type,
            instance=instance,
            label=node_data.get("label", f"New {entity_type}"),
            parent_id=parent_id,
        )

        # Recursively deserialize children
        for child_data in node_data.get("children", []):
            child = deserialize_node(child_data, parent_id=node.id)
            if child:
                node.children.append(child)
                state.nodes_by_id[child.id] = child

        return node

    state.entity_tree = []
    state.nodes_by_id = {}

    for node_data in data.get("tree", []):
        node = deserialize_node(node_data)
        if node:
            state.entity_tree.append(node)
            state.nodes_by_id[node.id] = node


def create_nested_nodes(
    state: AppState,
    facade: Any,
    parent_node: TreeNode,
    entity_type: str,
    data: dict[str, Any],
) -> None:
    """Recursively create child TreeNodes for nested entity data.

    This function processes nested fields (like studies, protocols, samples)
    in entity data and creates corresponding TreeNode children.

    Args:
        state: AppState to add nodes to.
        facade: ProfileFacade for creating entity instances.
        parent_node: Parent TreeNode to attach children to.
        entity_type: Type name of the parent entity.
        data: Raw dict data containing nested entity fields.
    """
    try:
        helper = getattr(facade, entity_type)
    except AttributeError:
        logger.warning(f"No helper for {entity_type}")
        return

    for field_name in helper.nested_fields:
        field_data = data.get(field_name)
        if not field_data:
            continue

        field_info = helper.field_info(field_name)
        field_type = field_info.get("type")
        nested_type = field_info.get("items")

        # Only process entity types (uppercase names), skip primitives
        if not nested_type or not nested_type[0].isupper():
            continue

        # Skip single entity fields (type: entity) - their data stays in parent instance
        # Only create child nodes for list fields (type: list)
        if field_type == "entity":
            continue

        items = field_data if isinstance(field_data, list) else [field_data]

        nested_helper = getattr(facade, nested_type, None)
        if not nested_helper:
            logger.warning(f"No helper for nested type {nested_type}")
            continue

        for item_data in items:
            # Handle Pydantic models by converting to dict
            if hasattr(item_data, "model_dump"):
                item_data = item_data.model_dump(exclude_none=True)
            # Skip non-dict items (primitives like strings)
            elif not isinstance(item_data, dict):
                continue

            try:
                child_node = add_entity_node(
                    state, nested_type, item_data, parent_id=parent_node.id, helper=nested_helper
                )

                # Set label from common identifier fields (falling back to a
                # first/last-name composite only when no identifier is present).
                label = get_label_from_values(item_data)
                if label:
                    child_node.label = label

                # Recursively process this child's nested fields
                create_nested_nodes(state, facade, child_node, nested_type, item_data)

            except Exception as e:
                logger.error(f"Failed to create {nested_type}: {e}")


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
                val = data.get(col, "")
                # Truncate long values for display
                if isinstance(val, str) and len(val) > 50:
                    val = val[:47] + "..."
                row_data[col] = val
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
    parent_type_lower = node.entity_type.lower()
    inherited_columns = set()
    for col in display_columns:
        if col.endswith("_id"):
            ref_type = col[:-3]  # Remove "_id" suffix
            if ref_type == parent_type_lower:
                inherited_columns.add(col)

    return {
        "columns": display_columns,
        "rows": rows,
        "column_types": column_types,
        "nested_entity_type": item_type,
        "inherited_columns": list(inherited_columns),
        "required_columns": list(required_columns),
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


def get_tree_data_from_nodes(state: AppState) -> list[dict[str, Any]]:
    """Get tree data from actual TreeNode children, not instance data.

    Unlike AppState.get_tree_data() which extracts nested items from
    instance.model_dump(), this function uses the actual TreeNode.children
    hierarchy that we build with create_nested_nodes().

    Args:
        state: AppState with populated entity_tree and nodes_by_id.

    Returns:
        List of tree item dicts with proper IDs that exist in nodes_by_id.
    """

    def node_to_dict(node: TreeNode) -> dict[str, Any]:
        """Convert TreeNode to dict including actual children."""
        result = {
            "id": node.id,
            "entity_type": node.entity_type,
            "label": node.label,
            "parent_id": node.parent_id,
            "has_children": bool(node.children),
            "children": [node_to_dict(c) for c in node.children],
            "is_nested": False,  # All TreeNodes have real IDs
        }
        return result

    return [node_to_dict(n) for n in state.entity_tree]


def get_dataset_state(dataset: Dataset) -> AppState:
    """Create AppState for a dataset from database.

    Args:
        dataset: Dataset model with profile, version, and data fields.

    Returns:
        AppState populated with dataset's entity tree.
    """
    state = AppState()
    state.profile = dataset.profile
    state.version = dataset.version
    if dataset.data:
        deserialize_tree(state, dataset.data)
    return state


async def ensure_dataset_facade(
    dataset: Dataset,
    session: Any,
) -> AppState:
    """Get dataset state and ensure facade is properly set for user-defined specs.

    For datasets using database-stored specs (spec_draft_id), this loads the spec
    and creates a MetaseedClient with from_spec(). For built-in profiles, it creates
    a standard client.

    Uses MetaseedClient.load() to populate the facade's internal entity store,
    which is required for operations like to_graph() and get_roots().

    Args:
        dataset: Dataset model with profile, version, and optional spec_draft_id.
        session: Database session for loading spec drafts.

    Returns:
        AppState with facade ready to use.
    """
    from metaseed import MetaseedClient

    from metaseed_hub.models import SpecDraft

    # Always load fresh from database - no caching
    state = AppState()
    state.profile = dataset.profile
    state.version = dataset.version

    client: MetaseedClient | None = None

    # Load spec from database FIRST if dataset uses a user-defined spec
    if dataset.spec_draft_id:
        try:
            spec_draft = await session.get(SpecDraft, dataset.spec_draft_id)
            if spec_draft and spec_draft.spec_data:
                # spec_data may be SpecBuilderState format with spec nested under "spec" key
                raw_data = spec_draft.spec_data
                if isinstance(raw_data, dict) and "spec" in raw_data:
                    raw_data = raw_data["spec"]
                # Create client from custom spec
                client = MetaseedClient.from_spec(raw_data)
                state.facade = client.facade
                # Update state.profile to match facade's lowercased version
                state.profile = state.facade.profile
                logger.debug(f"Loaded draft spec for dataset {dataset.id}: {dataset.profile}")
            else:
                logger.warning(
                    f"No spec data found for dataset {dataset.id} "
                    f"spec_draft_id={dataset.spec_draft_id}"
                )
        except Exception as e:
            logger.error(f"Failed to load spec for dataset {dataset.id}: {e}")
            # Don't re-raise - let downstream code handle missing facade
    else:
        # Built-in profile: create standard client
        try:
            client = MetaseedClient(dataset.profile, dataset.version)
            state.facade = client.facade
        except Exception as e:
            logger.error(f"Failed to load profile {dataset.profile}: {e}")

    # Load entities into facade's internal store using client.load()
    # This is required for to_graph() and other facade operations
    if client and dataset.data:
        try:
            count = client.load(dataset.data)
            logger.debug(f"Loaded {count} entities for dataset {dataset.id}")
            # Invalidate AppState cache to rebuild from facade
            state.invalidate_cache()
        except Exception as e:
            logger.error(f"Failed to load entities for dataset {dataset.id}: {e}")

    return state


async def save_dataset_state(
    session: Any,
    dataset: Dataset,
    state: AppState,
    user_id: str | None = None,
) -> None:
    """Save AppState entity tree to database and create a version.

    Args:
        session: Database session.
        dataset: Dataset model to update.
        state: AppState with entity tree to save.
        user_id: Optional user ID for version tracking.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm.attributes import flag_modified

    new_data = serialize_tree(state)

    # Only create version if data changed
    if new_data != dataset.data:
        # Get next version number
        result = await session.execute(
            select(func.coalesce(func.max(DatasetVersion.version_number), 0)).where(
                DatasetVersion.dataset_id == dataset.id
            )
        )
        max_version = result.scalar() or 0

        # Create version with new data
        version = DatasetVersion(
            dataset_id=dataset.id,
            version_number=max_version + 1,
            data=new_data,
            created_by_id=user_id,
        )
        session.add(version)

    dataset.data = new_data
    flag_modified(dataset, "data")
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)


def humanize_field_name(name: str) -> str:
    """Convert camelCase or snake_case field name to human-readable format.

    Examples:
        occurrenceID -> Occurrence ID
        basisOfRecord -> Basis Of Record
        unique_id -> Unique Id
    """
    if not name:
        return name
    # First replace underscores with spaces
    name = name.replace("_", " ")
    # Insert space before uppercase letters (for camelCase)
    result = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Handle consecutive uppercase (like ID, URL)
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", result)
    # Title case and return
    return result.title()


def escape_pattern_hyphen(pattern: str) -> str:
    """Escape hyphens in regex character classes for HTML pattern attribute.

    Modern browsers use RegExp 'v' flag which requires escaping hyphens
    that are not part of a valid range (like a-z or 0-9).
    Common problematic pattern: [A-Za-z0-9_-] where _- is not a valid range.
    """
    if not pattern:
        return pattern
    # Escape hyphens that follow underscore or other non-range chars
    # Pattern: _-] or _-x where x is not forming a valid range
    result = re.sub(r"(_)-(\])", r"\1\\-\2", pattern)
    result = re.sub(r"(_)-([^\]])", r"\1\\-\2", result)
    return result
