"""Graph data for dataset visualization, built on metaseed's public facade API."""

from typing import Any

from metaseed import ProfileFacade


def build_graph(facade: ProfileFacade) -> dict[str, Any]:
    """Build vis.js graph data for all entities held by a facade.

    Node and edge generation (including tooltips, containment edges, and dashed
    reference edges) is delegated to ``ProfileFacade.to_graph``. The profile's
    full entity-type list is added so the legend can show types that have no
    instances yet.

    Args:
        facade: Profile facade holding the dataset's entities.

    Returns:
        Dictionary with ``nodes``, ``edges``, and ``entity_types`` lists.
    """
    graph_data: dict[str, Any] = facade.to_graph()
    graph_data["entity_types"] = list(facade.entities)
    return graph_data
