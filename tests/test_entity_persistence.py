"""Entity persistence through the real save and load paths.

Exercises the production pipeline end to end against the database:
``ensure_dataset_facade`` (load), the facade write paths
(``add_entity_node``/``AppState.update_node``), and ``save_dataset_state``
(save). Serializer unit behavior is pinned in test_save_serializes_facade.py
and test_serialization_roundtrip.py; this file covers that entities — including
incomplete drafts — survive a save/reload cycle through PostgreSQL.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade, save_dataset_state
from metaseed_hub.ui.helpers.tree import add_entity_node

from .factories import make_dataset, make_tenant


async def _make_saved_dataset(session: AsyncSession):
    """Persist a tenant and an empty miappe dataset, returning the dataset."""
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()

    dataset = make_dataset(tenant=tenant, profile="miappe", version="1.2", data={})
    session.add(dataset)
    await session.flush()
    await session.refresh(dataset)
    return dataset


class TestEntityPersistence:
    """Save/reload round trips through the database."""

    async def test_save_and_reload_preserves_entities(self, session: AsyncSession) -> None:
        """A parent with a child survives save and reload intact."""
        dataset = await _make_saved_dataset(session)

        state = await ensure_dataset_facade(dataset, session)
        inv = add_entity_node(state, "Investigation", {"unique_id": "INV-1", "title": "T"})
        add_entity_node(
            state,
            "Study",
            {"unique_id": "STU-1", "title": "S", "investigation_id": "INV-1"},
            parent_id=inv.id,
        )
        await save_dataset_state(session, dataset, state)

        assert dataset.data["profile"] == "miappe"
        assert len(dataset.data["tree"]) == 1

        reloaded = await ensure_dataset_facade(dataset, session)
        root = reloaded.entity_tree[0]
        assert root.entity_type == "Investigation"
        assert root.instance.title == "T"
        assert [c.entity_type for c in root.children] == ["Study"]
        assert root.children[0].instance.unique_id == "STU-1"

    async def test_incomplete_draft_row_survives_save_and_reload(
        self, session: AsyncSession
    ) -> None:
        """The add-row -> save -> reload round trip keeps an incomplete draft.

        This is the flow that lost data in #54: a draft child (missing required
        fields) added next to a complete parent must still be there after the
        dataset is saved and loaded again.
        """
        dataset = await _make_saved_dataset(session)

        state = await ensure_dataset_facade(dataset, session)
        inv = add_entity_node(state, "Investigation", {"unique_id": "INV-1", "title": "T"})
        # No unique_id: an incomplete draft as the add-row button creates.
        add_entity_node(state, "Study", {"title": "draft"}, parent_id=inv.id)
        await save_dataset_state(session, dataset, state)

        reloaded = await ensure_dataset_facade(dataset, session)
        children = reloaded.entity_tree[0].children
        assert [c.entity_type for c in children] == ["Study"]
        assert children[0].instance.title == "draft"

    async def test_field_update_persists(self, session: AsyncSession) -> None:
        """An update written through the facade is the value read back."""
        dataset = await _make_saved_dataset(session)

        state = await ensure_dataset_facade(dataset, session)
        inv = add_entity_node(state, "Investigation", {"unique_id": "INV-1", "title": "Old"})
        await save_dataset_state(session, dataset, state)

        state.update_node(inv.id, {"unique_id": "INV-1", "title": "New"}, skip_validation=True)
        await save_dataset_state(session, dataset, state)

        reloaded = await ensure_dataset_facade(dataset, session)
        assert reloaded.entity_tree[0].instance.title == "New"

    async def test_update_parent_keeps_children(self, session: AsyncSession) -> None:
        """Updating a parent must not detach or drop its children."""
        dataset = await _make_saved_dataset(session)

        state = await ensure_dataset_facade(dataset, session)
        inv = add_entity_node(state, "Investigation", {"unique_id": "INV-1", "title": "T"})
        add_entity_node(
            state,
            "Study",
            {"unique_id": "STU-1", "title": "S", "investigation_id": "INV-1"},
            parent_id=inv.id,
        )
        state.update_node(inv.id, {"unique_id": "INV-1", "title": "Renamed"}, skip_validation=True)
        await save_dataset_state(session, dataset, state)

        reloaded = await ensure_dataset_facade(dataset, session)
        root = reloaded.entity_tree[0]
        assert root.instance.title == "Renamed"
        assert [c.entity_type for c in root.children] == ["Study"]

    async def test_three_level_hierarchy_round_trips(self, session: AsyncSession) -> None:
        """Investigation -> Study -> ObservationUnit survives save and reload."""
        dataset = await _make_saved_dataset(session)

        state = await ensure_dataset_facade(dataset, session)
        inv = add_entity_node(state, "Investigation", {"unique_id": "INV-1", "title": "T"})
        study = add_entity_node(
            state,
            "Study",
            {"unique_id": "STU-1", "title": "S", "investigation_id": "INV-1"},
            parent_id=inv.id,
        )
        add_entity_node(
            state,
            "ObservationUnit",
            {"unique_id": "OU-1", "study_id": "STU-1"},
            parent_id=study.id,
        )
        await save_dataset_state(session, dataset, state)

        reloaded = await ensure_dataset_facade(dataset, session)
        root = reloaded.entity_tree[0]
        assert root.entity_type == "Investigation"
        assert root.children[0].entity_type == "Study"
        assert root.children[0].children[0].entity_type == "ObservationUnit"
