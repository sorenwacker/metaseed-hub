"""Tests for EntityService.

Tests cover the entity save flow refactor ensuring:
1. Validation errors don't block saves
2. Missing/invalid specs return clear errors
3. Unknown entity types return clear errors
4. Database failures are handled gracefully
"""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import (
    Dataset,
    SpecDraft,
    Tenant,
    User,
    Workspace,
)
from metaseed_hub.ui.services import (
    EntityService,
    FacadeLoadError,
    SpecNotFoundError,
)


class TestEntityServiceWithDraftSpec:
    """Tests for EntityService with custom draft specs."""

    @pytest.mark.asyncio
    async def test_create_entity_with_validation_errors_still_saves(
        self, session: AsyncSession
    ) -> None:
        """Entity with validation errors should save with warnings returned."""
        # Setup: Create tenant, workspace, user, and spec draft
        tenant = Tenant(name="Test Tenant", slug="test-val")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        user = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="user@test.com",
            display_name="User",
        )
        session.add(user)
        await session.flush()

        # Create spec with required field
        spec_data = {
            "spec": {
                "name": "test_spec",
                "display_name": "Test Spec",
                "version": "1.0",
                "description": "Spec for testing",
                "root_entity": "Investigation",
                "entities": {
                    "Investigation": {
                        "description": "An investigation",
                        "fields": [
                            {
                                "name": "unique_id",
                                "type": "string",
                                "required": True,
                                "description": "Unique identifier",
                            },
                            {
                                "name": "title",
                                "type": "string",
                                "required": True,
                                "description": "Investigation title",
                            },
                        ],
                    },
                },
            }
        }

        spec_draft = SpecDraft(
            name="Test Spec",
            user_id=user.id,
            workspace_id=workspace.id,
            version="1.0",
            spec_data=spec_data,
        )
        session.add(spec_draft)
        await session.flush()

        # Create dataset using the spec
        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="test_spec",
            version="1.0",
            spec_draft_id=spec_draft.id,
            data={"profile": "test_spec", "version": "1.0", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        # Create service and save entity with missing required field
        service = EntityService(session, dataset)

        # This should save despite missing 'title' field
        result = await service.create_or_update_entity(
            entity_type="Investigation",
            values={"unique_id": "INV-001"},  # Missing 'title'
        )

        # Assert: Entity saved successfully
        assert result.success is True
        assert result.node_id is not None
        assert result.node is not None

        # Assert: Validation warnings returned
        assert len(result.validation_errors) > 0
        assert any("title" in err.lower() for err in result.validation_errors)

    @pytest.mark.asyncio
    async def test_missing_spec_draft_returns_clear_error(self, session: AsyncSession) -> None:
        """Dataset referencing deleted spec draft returns clear error.

        This simulates the scenario where a spec draft is deleted after
        a dataset was created referencing it. Due to ON DELETE SET NULL,
        the spec_draft_id becomes NULL. We test by creating and then deleting.
        """
        tenant = Tenant(name="Test Tenant", slug="test-mis")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        user = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="user@test.com",
            display_name="User",
        )
        session.add(user)
        await session.flush()

        # Create a real spec draft first
        spec_draft = SpecDraft(
            name="Will Be Deleted",
            user_id=user.id,
            workspace_id=workspace.id,
            version="1.0",
            spec_data={
                "spec": {
                    "name": "temp",
                    "display_name": "Temp",
                    "version": "1.0",
                    "entities": {},
                }
            },
        )
        session.add(spec_draft)
        await session.flush()
        spec_draft_id = spec_draft.id

        # Create dataset referencing the spec
        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="temp",
            version="1.0",
            spec_draft_id=spec_draft_id,
            data={"profile": "temp", "version": "1.0", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        # Now delete the spec draft (simulates user deleting their spec)
        await session.delete(spec_draft)
        await session.commit()

        # Refresh dataset to see the SET NULL effect
        await session.refresh(dataset)

        # With SET NULL, the dataset now has spec_draft_id=None but profile="temp"
        # Since it's a non-builtin profile with no spec_draft_id, facade creation fails
        service = EntityService(session, dataset)

        # This should fail since profile "temp" doesn't exist as built-in
        with pytest.raises((SpecNotFoundError, FacadeLoadError)):
            await service.ensure_state()

    @pytest.mark.asyncio
    async def test_empty_spec_data_returns_clear_error(self, session: AsyncSession) -> None:
        """Spec draft with empty spec_data returns clear error."""
        tenant = Tenant(name="Test Tenant", slug="test-emp")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        user = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="user@test.com",
            display_name="User",
        )
        session.add(user)
        await session.flush()

        # Create spec with empty data
        spec_draft = SpecDraft(
            name="Empty Spec",
            user_id=user.id,
            workspace_id=workspace.id,
            version="1.0",
            spec_data={},  # Empty!
        )
        session.add(spec_draft)
        await session.flush()

        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="empty_spec",
            version="1.0",
            spec_draft_id=spec_draft.id,
            data={"profile": "empty_spec", "version": "1.0", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        service = EntityService(session, dataset)

        with pytest.raises(SpecNotFoundError) as exc_info:
            await service.ensure_state()

        assert "empty" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_invalid_spec_structure_returns_clear_error(self, session: AsyncSession) -> None:
        """Spec draft with invalid structure returns clear error."""
        tenant = Tenant(name="Test Tenant", slug="test-inv")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        user = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="user@test.com",
            display_name="User",
        )
        session.add(user)
        await session.flush()

        # Create spec with invalid structure (missing required ProfileSpec fields)
        spec_draft = SpecDraft(
            name="Invalid Spec",
            user_id=user.id,
            workspace_id=workspace.id,
            version="1.0",
            spec_data={
                "spec": {
                    "name": "invalid",
                    # Missing: display_name, version, entities, etc.
                }
            },
        )
        session.add(spec_draft)
        await session.flush()

        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="invalid_spec",
            version="1.0",
            spec_draft_id=spec_draft.id,
            data={"profile": "invalid_spec", "version": "1.0", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        service = EntityService(session, dataset)

        with pytest.raises(FacadeLoadError) as exc_info:
            await service.ensure_state()

        # Error message mentions profile initialization failure
        assert "could not initialize" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_unknown_entity_type_returns_clear_error(self, session: AsyncSession) -> None:
        """Unknown entity type returns clear error."""
        tenant = Tenant(name="Test Tenant", slug="test-unk")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        user = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="user@test.com",
            display_name="User",
        )
        session.add(user)
        await session.flush()

        spec_data = {
            "spec": {
                "name": "test_spec",
                "display_name": "Test Spec",
                "version": "1.0",
                "description": "Spec for testing",
                "root_entity": "Investigation",
                "entities": {
                    "Investigation": {
                        "description": "An investigation",
                        "fields": [
                            {
                                "name": "unique_id",
                                "type": "string",
                                "required": True,
                                "description": "Unique identifier",
                            },
                        ],
                    },
                },
            }
        }

        spec_draft = SpecDraft(
            name="Test Spec",
            user_id=user.id,
            workspace_id=workspace.id,
            version="1.0",
            spec_data=spec_data,
        )
        session.add(spec_draft)
        await session.flush()

        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="test_spec",
            version="1.0",
            spec_draft_id=spec_draft.id,
            data={"profile": "test_spec", "version": "1.0", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        service = EntityService(session, dataset)
        await service.ensure_state()

        # Try to create an entity type that doesn't exist
        result = await service.create_or_update_entity(
            entity_type="NonExistentEntity",
            values={"some_field": "value"},
        )

        assert result.success is False
        assert "NonExistentEntity" in result.error_message
        assert "Unknown entity type" in result.error_message


class TestEntityServiceWithBuiltinProfile:
    """Tests for EntityService with built-in profiles."""

    @pytest.mark.asyncio
    async def test_builtin_profile_loads_successfully(self, session: AsyncSession) -> None:
        """Built-in profile (miappe) loads correctly without spec_draft_id."""
        tenant = Tenant(name="Test Tenant", slug="test-blt")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        # Dataset using built-in miappe profile
        dataset = Dataset(
            name="MIAPPE Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
            data={"profile": "miappe", "version": "1.2", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        service = EntityService(session, dataset)
        state = await service.ensure_state()

        assert state is not None
        assert state.profile == "miappe"
        assert service.facade is not None

        # Verify we can access built-in entity types
        helper = service.get_helper("Investigation")
        assert helper is not None


class TestEntityServiceDelete:
    """Tests for entity deletion."""

    @pytest.mark.asyncio
    async def test_delete_existing_entity_succeeds(self, session: AsyncSession) -> None:
        """Deleting an existing entity succeeds."""
        tenant = Tenant(name="Test Tenant", slug="test-del")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        # Dataset using built-in miappe profile
        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
            data={"profile": "miappe", "version": "1.2", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        service = EntityService(session, dataset)

        # Create an entity first
        create_result = await service.create_or_update_entity(
            entity_type="Investigation",
            values={"unique_id": "INV-001", "title": "Test Investigation"},
        )
        assert create_result.success is True
        node_id = create_result.node_id

        # Delete the entity
        delete_result = await service.delete_entity(node_id)
        assert delete_result.success is True

        # Verify entity is gone
        state = service.state
        assert node_id not in state.nodes_by_id

    @pytest.mark.asyncio
    async def test_delete_nonexistent_entity_returns_error(self, session: AsyncSession) -> None:
        """Deleting a non-existent entity returns clear error."""
        tenant = Tenant(name="Test Tenant", slug="test-dne")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
            data={"profile": "miappe", "version": "1.2", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        service = EntityService(session, dataset)
        await service.ensure_state()

        # Try to delete non-existent entity
        result = await service.delete_entity("non-existent-node-id")

        assert result.success is False
        assert "not found" in result.error_message.lower()


class TestEntityServiceUpdate:
    """Tests for entity updates."""

    @pytest.mark.asyncio
    async def test_update_existing_entity(self, session: AsyncSession) -> None:
        """Updating an existing entity works correctly."""
        tenant = Tenant(name="Test Tenant", slug="test-upd")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
            data={"profile": "miappe", "version": "1.2", "tree": []},
        )
        session.add(dataset)
        await session.commit()

        service = EntityService(session, dataset)

        # Create entity
        create_result = await service.create_or_update_entity(
            entity_type="Investigation",
            values={"unique_id": "INV-001", "title": "Original Title"},
        )
        assert create_result.success is True
        node_id = create_result.node_id

        # Update entity
        update_result = await service.create_or_update_entity(
            entity_type="Investigation",
            values={"unique_id": "INV-001", "title": "Updated Title"},
            node_id=node_id,
        )

        assert update_result.success is True
        assert update_result.node_id == node_id

        # Verify update
        state = service.state
        node = state.nodes_by_id[node_id]
        assert node.instance.title == "Updated Title"
