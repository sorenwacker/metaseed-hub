"""Tests for entity save and load functionality in the Hub UI.

Note: serialize_tree and deserialize_tree are defined inside create_hub_app()
so we test them indirectly through the app or by recreating the logic here.
"""

from metaseed.ui.state import AppState, TreeNode
from sqlalchemy.ext.asyncio import AsyncSession

from .factories import make_dataset, make_tenant, make_workspace


# Recreate serialize/deserialize logic for testing
# These mirror the functions in app.py
def serialize_tree(state: AppState) -> dict:
    """Serialize AppState entity tree to JSON-compatible dict."""

    def serialize_node(node: TreeNode) -> dict:
        node_data = {
            "id": node.id,
            "entity_type": node.entity_type,
            "label": node.label,
            "parent_id": node.parent_id,
            "data": {},
        }
        if node.instance and hasattr(node.instance, "model_dump"):
            node_data["data"] = node.instance.model_dump(exclude_none=True)
        if node.children:
            node_data["children"] = [serialize_node(c) for c in node.children]
        return node_data

    return {
        "profile": state.profile,
        "version": state.version,
        "tree": [serialize_node(n) for n in state.entity_tree],
    }


def deserialize_tree(state: AppState, data: dict | None) -> None:
    """Deserialize JSON data into AppState entity tree."""
    if not data or "tree" not in data:
        state.entity_tree = []
        state.nodes_by_id = {}
        return

    # For testing, we create simple nodes without full facade
    def deserialize_node(node_data: dict, parent_id: str | None = None) -> TreeNode | None:
        entity_type = node_data.get("entity_type")
        if not entity_type:
            return None

        # Create a simple instance holder
        class DataHolder:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        node = TreeNode(
            id=node_data.get("id", ""),
            entity_type=entity_type,
            instance=DataHolder(node_data.get("data", {})),
            label=node_data.get("label", f"New {entity_type}"),
            parent_id=parent_id,
        )

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


class TestSerializeDeserializeTree:
    """Tests for entity tree serialization and deserialization."""

    def test_serialize_empty_tree(self) -> None:
        """Empty AppState serializes to empty tree."""
        state = AppState()
        state.profile = "test"
        state.version = "1.0"
        state.entity_tree = []
        state.nodes_by_id = {}

        result = serialize_tree(state)

        assert result["profile"] == "test"
        assert result["version"] == "1.0"
        assert result["tree"] == []

    def test_serialize_single_node(self) -> None:
        """Single root node serializes correctly."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"

        # Create a mock instance with model_dump
        class MockInstance:
            def model_dump(self, exclude_none: bool = False) -> dict:
                return {"unique_id": "inv-001", "title": "Test Investigation"}

        node = TreeNode(
            id="node-1",
            entity_type="Investigation",
            instance=MockInstance(),
            label="Test Investigation",
            parent_id=None,
        )
        state.entity_tree = [node]
        state.nodes_by_id = {"node-1": node}

        result = serialize_tree(state)

        assert len(result["tree"]) == 1
        serialized_node = result["tree"][0]
        assert serialized_node["id"] == "node-1"
        assert serialized_node["entity_type"] == "Investigation"
        assert serialized_node["label"] == "Test Investigation"
        assert serialized_node["data"]["unique_id"] == "inv-001"
        assert serialized_node["data"]["title"] == "Test Investigation"

    def test_serialize_nested_nodes(self) -> None:
        """Nested nodes serialize with children."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"

        class MockInvestigation:
            def model_dump(self, exclude_none: bool = False) -> dict:
                return {"unique_id": "inv-001", "title": "Parent"}

        class MockStudy:
            def model_dump(self, exclude_none: bool = False) -> dict:
                return {"unique_id": "study-001", "title": "Child Study"}

        parent = TreeNode(
            id="parent-1",
            entity_type="Investigation",
            instance=MockInvestigation(),
            label="Parent",
            parent_id=None,
        )
        child = TreeNode(
            id="child-1",
            entity_type="Study",
            instance=MockStudy(),
            label="Child Study",
            parent_id="parent-1",
        )
        parent.children = [child]

        state.entity_tree = [parent]
        state.nodes_by_id = {"parent-1": parent, "child-1": child}

        result = serialize_tree(state)

        assert len(result["tree"]) == 1
        parent_data = result["tree"][0]
        assert parent_data["id"] == "parent-1"
        assert len(parent_data.get("children", [])) == 1

        child_data = parent_data["children"][0]
        assert child_data["id"] == "child-1"
        assert child_data["entity_type"] == "Study"
        assert child_data["parent_id"] == "parent-1"

    def test_deserialize_empty_tree(self) -> None:
        """Empty tree data deserializes to empty state."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"

        deserialize_tree(state, {"tree": []})

        assert state.entity_tree == []
        assert state.nodes_by_id == {}

    def test_deserialize_preserves_none_data(self) -> None:
        """Deserialization handles None data gracefully."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"

        deserialize_tree(state, None)

        assert state.entity_tree == []
        # nodes_by_id may be empty or unchanged

    def test_round_trip_preserves_data(self) -> None:
        """Serialize then deserialize preserves entity data."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"

        class MockInstance:
            def model_dump(self, exclude_none: bool = False) -> dict:
                return {"unique_id": "test-123", "title": "Round Trip Test"}

        node = TreeNode(
            id="node-rt",
            entity_type="Investigation",
            instance=MockInstance(),
            label="Round Trip Test",
            parent_id=None,
        )
        state.entity_tree = [node]
        state.nodes_by_id = {"node-rt": node}

        # Serialize
        serialized = serialize_tree(state)

        # Create new state and deserialize
        new_state = AppState()
        new_state.profile = "miappe"
        new_state.version = "1.1"
        deserialize_tree(new_state, serialized)

        # Verify
        assert len(new_state.entity_tree) == 1
        restored_node = new_state.entity_tree[0]
        assert restored_node.id == "node-rt"
        assert restored_node.entity_type == "Investigation"
        assert restored_node.label == "Round Trip Test"


class TestEntityPersistence:
    """Tests for entity persistence to database."""

    async def test_save_and_load_dataset_state(self, session: AsyncSession) -> None:
        """Saving and loading dataset state preserves entity data."""
        # Setup
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        dataset = make_dataset(workspace=workspace, profile="miappe", version="1.1")
        session.add(dataset)
        await session.flush()
        await session.refresh(dataset)

        # Create state directly (mimics what get_dataset_state does)
        state = AppState()
        state.profile = dataset.profile
        state.version = dataset.version
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def model_dump(self, exclude_none: bool = False) -> dict:
                return {"unique_id": "persist-test", "title": "Persistence Test"}

        node = TreeNode(
            id="persist-node",
            entity_type="Investigation",
            instance=MockInstance(),
            label="Persistence Test",
            parent_id=None,
        )
        state.entity_tree.append(node)
        state.nodes_by_id["persist-node"] = node

        # Save to database
        from sqlalchemy.orm.attributes import flag_modified

        dataset.data = serialize_tree(state)
        flag_modified(dataset, "data")
        await session.commit()
        await session.refresh(dataset)

        # Verify data is in dataset.data
        assert dataset.data is not None
        assert "tree" in dataset.data
        assert len(dataset.data["tree"]) == 1
        assert dataset.data["tree"][0]["id"] == "persist-node"
        assert dataset.data["tree"][0]["data"]["title"] == "Persistence Test"

        # Test loading back
        new_state = AppState()
        new_state.profile = dataset.profile
        new_state.version = dataset.version
        deserialize_tree(new_state, dataset.data)

        assert len(new_state.entity_tree) == 1
        loaded_node = new_state.entity_tree[0]
        assert loaded_node.id == "persist-node"
        assert loaded_node.label == "Persistence Test"
        assert loaded_node.instance.model_dump()["title"] == "Persistence Test"


class TestFieldUpdates:
    """Tests for updating entity fields."""

    def test_update_node_instance_preserves_in_tree(self) -> None:
        """Updating a node's instance should be reflected when serializing."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"

        class MockInstance:
            def __init__(self, title: str):
                self._title = title

            def model_dump(self, exclude_none: bool = False) -> dict:
                return {"unique_id": "test-1", "title": self._title}

        # Create initial node
        node = TreeNode(
            id="node-1",
            entity_type="Investigation",
            instance=MockInstance("Original Title"),
            label="Original Title",
            parent_id=None,
        )
        state.entity_tree = [node]
        state.nodes_by_id = {"node-1": node}

        # Verify original value
        serialized = serialize_tree(state)
        assert serialized["tree"][0]["data"]["title"] == "Original Title"

        # Update the node's instance (simulating a save)
        node.instance = MockInstance("New Title")
        node.label = "New Title"

        # Serialize again - should have new value
        serialized_after = serialize_tree(state)
        assert serialized_after["tree"][0]["data"]["title"] == "New Title"
        assert serialized_after["tree"][0]["label"] == "New Title"

    def test_update_nested_node_preserves_in_tree(self) -> None:
        """Updating a nested node should be reflected when serializing."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        # Create parent and child
        parent = TreeNode(
            id="parent-1",
            entity_type="Investigation",
            instance=MockInstance({"unique_id": "inv-1", "title": "Parent"}),
            label="Parent",
            parent_id=None,
        )
        child = TreeNode(
            id="child-1",
            entity_type="Study",
            instance=MockInstance({"unique_id": "study-1", "title": "Original Study"}),
            label="Original Study",
            parent_id="parent-1",
        )
        parent.children = [child]

        state.entity_tree = [parent]
        state.nodes_by_id = {"parent-1": parent, "child-1": child}

        # Update child via nodes_by_id (how the UI does it)
        state.nodes_by_id["child-1"].instance = MockInstance(
            {"unique_id": "study-1", "title": "Updated Study"}
        )
        state.nodes_by_id["child-1"].label = "Updated Study"

        # Serialize - child should have new value
        serialized = serialize_tree(state)
        child_data = serialized["tree"][0]["children"][0]
        assert child_data["data"]["title"] == "Updated Study"
        assert child_data["label"] == "Updated Study"

    async def test_field_update_persists_to_database(self, session: AsyncSession) -> None:
        """Field updates should persist to database and reload correctly."""
        # Setup
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        dataset = make_dataset(workspace=workspace, profile="miappe", version="1.1")
        session.add(dataset)
        await session.flush()
        await session.refresh(dataset)

        # Create initial state with entity
        state = AppState()
        state.profile = dataset.profile
        state.version = dataset.version
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def __init__(self, title: str):
                self._title = title

            def model_dump(self, exclude_none: bool = False) -> dict:
                return {"unique_id": "field-test", "title": self._title}

        node = TreeNode(
            id="field-node",
            entity_type="Investigation",
            instance=MockInstance("Initial Title"),
            label="Initial Title",
            parent_id=None,
        )
        state.entity_tree.append(node)
        state.nodes_by_id["field-node"] = node

        # Save initial state
        from sqlalchemy.orm.attributes import flag_modified

        dataset.data = serialize_tree(state)
        flag_modified(dataset, "data")
        await session.commit()
        await session.refresh(dataset)

        # Verify initial save
        assert dataset.data["tree"][0]["data"]["title"] == "Initial Title"

        # Simulate field update (like UI would do)
        node.instance = MockInstance("Updated Title")
        node.label = "Updated Title"

        # Save updated state
        dataset.data = serialize_tree(state)
        flag_modified(dataset, "data")
        await session.commit()
        await session.refresh(dataset)

        # Verify update persisted
        assert dataset.data["tree"][0]["data"]["title"] == "Updated Title"
        assert dataset.data["tree"][0]["label"] == "Updated Title"

        # Simulate server restart - create new state and load from DB
        new_state = AppState()
        new_state.profile = dataset.profile
        new_state.version = dataset.version
        deserialize_tree(new_state, dataset.data)

        # Verify loaded state has updated values
        assert len(new_state.entity_tree) == 1
        loaded_node = new_state.entity_tree[0]
        assert loaded_node.label == "Updated Title"
        assert loaded_node.instance.model_dump()["title"] == "Updated Title"


class TestInlineTablePersistence:
    """Tests for inline table (child entity) persistence."""

    def test_add_child_via_add_node(self) -> None:
        """Adding a child via add_node should link it to parent correctly."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        # Add parent node
        parent = state.add_node(
            "Investigation",
            MockInstance({"unique_id": "inv-1", "title": "Parent"}),
        )
        parent.label = "Parent"

        # Add child node (like add_table_row does)
        child = state.add_node(
            "Study",
            MockInstance({"unique_id": "study-1", "title": "Child Study"}),
            parent_id=parent.id,
        )
        child.label = "Child Study"

        # Verify child is in parent's children
        assert len(parent.children) == 1
        assert parent.children[0].id == child.id

        # Verify child is in nodes_by_id
        assert child.id in state.nodes_by_id

        # Serialize and verify structure
        serialized = serialize_tree(state)
        assert len(serialized["tree"]) == 1
        parent_data = serialized["tree"][0]
        assert len(parent_data.get("children", [])) == 1
        assert parent_data["children"][0]["id"] == child.id

    def test_child_survives_round_trip(self) -> None:
        """Child nodes should persist through serialize/deserialize cycle."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        # Create parent with child
        parent = state.add_node(
            "Investigation",
            MockInstance({"unique_id": "inv-1", "title": "Parent"}),
        )
        child = state.add_node(
            "Study",
            MockInstance({"unique_id": "study-1", "study_id": "inv-1", "title": "Child"}),
            parent_id=parent.id,
        )

        # Serialize
        serialized = serialize_tree(state)

        # Deserialize into new state (simulates server restart)
        new_state = AppState()
        new_state.profile = "miappe"
        new_state.version = "1.1"
        deserialize_tree(new_state, serialized)

        # Verify parent exists
        assert len(new_state.entity_tree) == 1
        loaded_parent = new_state.entity_tree[0]
        assert loaded_parent.id == parent.id

        # Verify child exists and is linked
        assert len(loaded_parent.children) == 1
        loaded_child = loaded_parent.children[0]
        assert loaded_child.id == child.id
        assert loaded_child.parent_id == parent.id

        # Verify both are in nodes_by_id
        assert parent.id in new_state.nodes_by_id
        assert child.id in new_state.nodes_by_id

    def test_update_parent_preserves_children(self) -> None:
        """Updating parent instance should not affect children."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        # Create parent with child
        parent = state.add_node(
            "Investigation",
            MockInstance({"unique_id": "inv-1", "title": "Original Parent"}),
        )
        child = state.add_node(
            "Study",
            MockInstance({"unique_id": "study-1", "title": "Child Study"}),
            parent_id=parent.id,
        )

        # Update parent instance (simulates form save)
        state.update_node(
            parent.id,
            MockInstance({"unique_id": "inv-1", "title": "Updated Parent"}),
        )

        # Verify child still exists
        assert len(parent.children) == 1
        assert parent.children[0].id == child.id

        # Serialize and verify child is still there
        serialized = serialize_tree(state)
        parent_data = serialized["tree"][0]
        assert parent_data["data"]["title"] == "Updated Parent"
        assert len(parent_data.get("children", [])) == 1
        assert parent_data["children"][0]["data"]["title"] == "Child Study"


class TestExampleLoading:
    """Tests for loading example data with nested entities."""

    def test_nested_items_become_child_nodes(self) -> None:
        """Nested items in example data should become child TreeNodes."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        # Simulate loading example with nested data
        # Parent node
        parent = state.add_node(
            "Investigation",
            MockInstance(
                {
                    "unique_id": "inv-001",
                    "title": "Test Investigation",
                }
            ),
        )

        # Add nested items as child nodes (like the fixed load_example does)
        nested_items = [
            {"unique_id": "study-001", "title": "Study 1"},
            {"unique_id": "study-002", "title": "Study 2"},
        ]
        for item_data in nested_items:
            state.add_node(
                "Study",
                MockInstance(item_data),
                parent_id=parent.id,
            )

        # Verify children were created
        assert len(parent.children) == 2
        assert parent.children[0].entity_type == "Study"
        assert parent.children[1].entity_type == "Study"

        # Verify serialization includes children
        serialized = serialize_tree(state)
        parent_data = serialized["tree"][0]
        assert len(parent_data.get("children", [])) == 2
        assert parent_data["children"][0]["data"]["title"] == "Study 1"
        assert parent_data["children"][1]["data"]["title"] == "Study 2"

    def test_nested_items_survive_round_trip(self) -> None:
        """Nested items should persist through save/load cycle."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        # Create parent with nested children
        parent = state.add_node(
            "Investigation",
            MockInstance({"unique_id": "inv-001", "title": "Parent"}),
        )
        for i in range(3):
            state.add_node(
                "Study",
                MockInstance({"unique_id": f"study-{i}", "title": f"Study {i}"}),
                parent_id=parent.id,
            )

        # Serialize (save to DB)
        serialized = serialize_tree(state)

        # Deserialize into new state (load from DB)
        new_state = AppState()
        new_state.profile = "miappe"
        new_state.version = "1.1"
        deserialize_tree(new_state, serialized)

        # Verify parent exists
        assert len(new_state.entity_tree) == 1
        loaded_parent = new_state.entity_tree[0]

        # Verify all children exist
        assert len(loaded_parent.children) == 3
        for i, child in enumerate(loaded_parent.children):
            assert child.entity_type == "Study"
            assert child.instance.model_dump()["title"] == f"Study {i}"

    def test_deeply_nested_items(self) -> None:
        """Deeply nested items (3+ levels) should work correctly."""
        state = AppState()
        state.profile = "miappe"
        state.version = "1.1"
        state.entity_tree = []
        state.nodes_by_id = {}

        class MockInstance:
            def __init__(self, data: dict):
                self._data = data

            def model_dump(self, exclude_none: bool = False) -> dict:
                return self._data

        # Create 3-level hierarchy: Investigation -> Study -> ObservationUnit
        investigation = state.add_node(
            "Investigation",
            MockInstance({"unique_id": "inv-001", "title": "Investigation"}),
        )
        study = state.add_node(
            "Study",
            MockInstance({"unique_id": "study-001", "title": "Study"}),
            parent_id=investigation.id,
        )
        obs_unit = state.add_node(
            "ObservationUnit",
            MockInstance({"unique_id": "ou-001", "title": "Observation Unit"}),
            parent_id=study.id,
        )

        # Verify hierarchy
        assert len(investigation.children) == 1
        assert investigation.children[0].id == study.id
        assert len(study.children) == 1
        assert study.children[0].id == obs_unit.id

        # Serialize and verify structure
        serialized = serialize_tree(state)
        inv_data = serialized["tree"][0]
        assert len(inv_data["children"]) == 1

        study_data = inv_data["children"][0]
        assert study_data["entity_type"] == "Study"
        assert len(study_data["children"]) == 1

        ou_data = study_data["children"][0]
        assert ou_data["entity_type"] == "ObservationUnit"

        # Round trip
        new_state = AppState()
        new_state.profile = "miappe"
        new_state.version = "1.1"
        deserialize_tree(new_state, serialized)

        # Verify 3-level hierarchy restored
        loaded_inv = new_state.entity_tree[0]
        loaded_study = loaded_inv.children[0]
        loaded_ou = loaded_study.children[0]

        assert loaded_inv.entity_type == "Investigation"
        assert loaded_study.entity_type == "Study"
        assert loaded_ou.entity_type == "ObservationUnit"


class TestValidation:
    """Tests for entity validation via metaseed."""

    def test_validation_placeholder(self) -> None:
        """Placeholder for validation tests.

        Validation is handled by metaseed's ProfileFacade.
        When creating an entity via helper.create(**values),
        Pydantic validation runs automatically.

        Full validation tests should:
        1. Test required field validation
        2. Test type coercion
        3. Test constraint validation (min/max, pattern, enum)
        4. Test nested entity validation
        """
        # TODO: Add comprehensive validation tests
        pass
