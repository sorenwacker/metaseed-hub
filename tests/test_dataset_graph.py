"""Tests for the dataset graph API's node_id subtree filtering.

The graph page links carry ``?node_id=...`` (partials/entity_form.html), but the
API previously ignored the parameter and always returned the full graph.
"""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import add_entity_node, save_dataset_state
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade
from metaseed_hub.ui.routes.dataset.editor import (
    _filter_graph_to_subtree,
    dataset_graph_api,
)
from tests.factories import make_dataset, make_tenant, make_user

_GRAPH = {
    "nodes": [{"id": "root"}, {"id": "child"}, {"id": "grandchild"}, {"id": "other"}],
    "edges": [
        {"from": "root", "to": "child"},
        {"from": "child", "to": "grandchild"},
        {"from": "root", "to": "other"},
    ],
    "entity_types": ["A", "B"],
}


def test_filter_keeps_node_and_descendants() -> None:
    filtered = _filter_graph_to_subtree(_GRAPH, "child")

    assert [n["id"] for n in filtered["nodes"]] == ["child", "grandchild"]
    assert filtered["edges"] == [{"from": "child", "to": "grandchild"}]
    assert filtered["entity_types"] == ["A", "B"]


def test_filter_with_unknown_node_returns_full_graph() -> None:
    """A stale link renders the whole graph rather than an empty canvas."""
    assert _filter_graph_to_subtree(_GRAPH, "gone") == _GRAPH


async def _dataset_with_tree(session: AsyncSession) -> tuple[Dataset, TokenUser, str]:
    """A miappe dataset with Investigation -> Study; returns the Study node id."""
    sub = f"grapher-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub))
    dataset = make_dataset(tenant=tenant, profile="miappe", version="1.1")
    session.add(dataset)
    await session.commit()

    state = await ensure_dataset_facade(dataset, session)
    inv = add_entity_node(state, "Investigation", {"title": "I"})
    study = add_entity_node(state, "Study", {"title": "S"}, parent_id=inv.id)
    token = TokenUser(sub=sub, email="g@example.org", name="G", roles=[])
    await save_dataset_state(session, dataset, state, token)
    return dataset, token, study.id


async def test_graph_api_filters_to_requested_subtree(session: AsyncSession) -> None:
    dataset, token, study_id = await _dataset_with_tree(session)

    response = await dataset_graph_api(dataset.id, session, token, node_id=study_id)

    graph = json.loads(response.body)
    assert [n["id"] for n in graph["nodes"]] == [study_id]
    assert graph["edges"] == []


async def test_graph_api_without_node_id_returns_full_graph(session: AsyncSession) -> None:
    dataset, token, _ = await _dataset_with_tree(session)

    response = await dataset_graph_api(dataset.id, session, token)

    graph = json.loads(response.body)
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
