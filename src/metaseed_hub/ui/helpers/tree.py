"""Entity-tree construction and JSON (de)serialization for AppState."""

import logging
import uuid
from datetime import date, datetime
from typing import Any

from metaseed.ui.state import AppState, TreeNode

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


def serialize_tree(state: AppState) -> dict[str, Any]:
    """Serialize AppState entity tree to JSON-compatible dict.

    Writes the same ``dataset.data`` column as metaseed's
    ``MetaseedClient.serialize(format="tree")`` (used by EntityService), so the
    two MUST stay format-compatible: a ``{profile, version, tree: [...]}``
    envelope with ``id``/``entity_type``/``label``/``data``/``children`` nodes.
    ``tests/test_serialization_roundtrip.py`` enforces that equivalence.

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
