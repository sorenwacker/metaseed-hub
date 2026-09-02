"""Entity-tree construction and JSON (de)serialization for AppState."""

import logging
from datetime import date, datetime
from typing import Any

from metaseed_hub.ui.metaseed_ui import AppState, TreeNode

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
    """Add an entity node through the facade without validation.

    Writes the (possibly incomplete) draft to the facade via ``state.add_node``
    with ``skip_validation`` -- so the entity lands in the source of truth rather
    than only the TreeNode cache -- and ``add_node`` keeps the cache consistent.
    ``skip_validation`` allows saving entities with missing required fields.

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
    instance = helper.model.model_construct(**data)

    return state.add_node(entity_type, instance, parent_id=parent_id, skip_validation=True)


class CacheDesyncError(RuntimeError):
    """The TreeNode cache holds nodes that are missing from the facade.

    A facade-based save would silently drop such nodes (the #54 data-loss
    incident). No production flow creates cache-only nodes anymore; if one
    reappears, serialization must fail loudly instead of losing the data.
    """


def serialize_tree(state: AppState) -> dict[str, Any]:
    """Serialize the dataset to a JSON-compatible ``{profile, version, tree}`` dict.

    Delegates to metaseed's ``MetaseedClient.serialize(format="tree")`` via the
    state's facade — the source of truth for entities — so the hub writes one
    format through one serializer (the same one EntityService uses). Every
    mutation flow writes through the facade, which is what makes this safe;
    the cache-consistency check below guards that invariant.

    Args:
        state: AppState whose facade holds the entities to serialize.

    Returns:
        Dictionary that can be stored as JSONB in the database.

    Raises:
        CacheDesyncError: If the TreeNode cache holds nodes the facade does
            not, meaning a facade-based save would silently drop them.
    """
    from metaseed import MetaseedClient

    facade = state.get_or_create_facade()
    serialized: dict[str, Any] = make_json_serializable(
        MetaseedClient.from_facade(facade).serialize(format="tree")
    )

    serialized_ids: set[str] = set()

    def collect_ids(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            serialized_ids.add(node["id"])
            collect_ids(node.get("children", []))

    collect_ids(serialized["tree"])
    stray_ids = set(state.nodes_by_id) - serialized_ids
    if stray_ids:
        raise CacheDesyncError(
            f"Refusing to save: {len(stray_ids)} cached node(s) are not in the "
            f"facade and would be lost: {sorted(stray_ids)[:5]}"
        )
    return serialized


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


def count_entities_by_type(tree: list[dict[str, Any]]) -> dict[str, int]:
    """Count the entities in a serialized tree, keyed by entity type.

    Walks ``children`` recursively, so nested entities count as well as roots.
    Serves the version diff and the home page cards from one definition.

    Args:
        tree: A dataset's ``data["tree"]``: root nodes with nested ``children``.

    Returns:
        ``{entity_type: count}`` in order of first appearance.
    """
    counts: dict[str, int] = {}
    for node in tree:
        entity_type = node.get("entity_type", "Unknown")
        counts[entity_type] = counts.get(entity_type, 0) + 1
        for child_type, child_count in count_entities_by_type(node.get("children", [])).items():
            counts[child_type] = counts.get(child_type, 0) + child_count
    return counts
