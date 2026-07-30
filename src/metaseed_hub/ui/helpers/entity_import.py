"""Turning parsed import payloads into entity nodes on an AppState.

Shared by the create-import and add-to-existing import routes so the grouping
and node-creation logic lives in one place.
"""

import logging
from typing import Any

from metaseed_hub.ui.helpers.tree import add_entity_node
from metaseed_hub.ui.metaseed_ui import AppState

logger = logging.getLogger("metaseed_hub")


def group_entities_by_type(
    entities: list[dict[str, Any]], default_type: str
) -> dict[str, list[dict[str, Any]]]:
    """Group exported entity payloads by their ``_type`` marker.

    This is the shape ``MetaseedClient.serialize()`` produces (an ``entities``
    list where each payload carries ``_type``), so importing it round-trips an
    export.

    Args:
        entities: Entity payloads from an export file's ``entities`` list.
        default_type: Type for payloads without a ``_type`` marker; callers pass
            the profile's root entity type since an unmarked payload can only be
            placed at the root.

    Returns:
        Mapping of entity type to its payloads, in input order.
    """
    by_type: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_type.setdefault(str(entity.get("_type", default_type)), []).append(entity)
    return by_type


def add_entities_in_order(
    state: AppState,
    facade: Any,
    entities_by_type: dict[str, list[dict[str, Any]]],
    root_entity: str,
) -> tuple[int, list[str]]:
    """Add parsed entities to ``state``, root entity type first.

    Metadata keys (``_type``, ``_node_id``) and empty values are dropped from
    each payload before the node is created. Entity types the profile does not
    declare are reported as errors rather than silently skipped.

    Args:
        state: Dataset state to add nodes to.
        facade: Profile facade listing the valid entity types.
        entities_by_type: Parsed payloads grouped by entity type.
        root_entity: The profile's root entity type, processed first so children
            can attach to it.

    Returns:
        Tuple of (number of entities added, per-entity error messages).
    """
    imported = 0
    errors: list[str] = []
    entity_order = [root_entity] + [e for e in facade.entities if e != root_entity]

    for entity_type in sorted(set(entities_by_type) - set(entity_order)):
        count = len(entities_by_type[entity_type])
        errors.append(f"{entity_type}: not an entity type of this profile ({count} skipped)")

    for entity_type in entity_order:
        for entity_data in entities_by_type.get(entity_type, []):
            try:
                clean_data = {
                    k: v
                    for k, v in entity_data.items()
                    if v is not None and str(v).strip() and not k.startswith("_")
                }
                if clean_data:
                    add_entity_node(state, entity_type, clean_data)
                    imported += 1
            except Exception as e:
                # One bad payload must not abort the rest of the import.
                errors.append(f"{entity_type}: {e}")
    return imported, errors
