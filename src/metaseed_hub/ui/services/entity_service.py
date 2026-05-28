"""Entity service for Hub UI operations.

This module provides a service layer for entity CRUD operations that:
- Guarantees facade is valid before operations
- Always saves entities (validation errors become warnings, not blockers)
- Returns user-friendly error messages instead of 500 errors
- Provides comprehensive logging
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from metaseed.facade import ProfileFacade
from metaseed.specs.schema import ProfileSpec
from metaseed.ui.state import AppState, TreeNode
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset, SpecDraft

from .exceptions import (
    EntityServiceError,
    EntityTypeNotFoundError,
    FacadeLoadError,
    SpecNotFoundError,
)

logger = logging.getLogger("metaseed_hub")


@dataclass
class EntitySaveResult:
    """Result of an entity save operation.

    Attributes:
        success: Whether the save operation succeeded.
        node_id: ID of the created/updated node, if successful.
        node: The TreeNode object, if successful.
        validation_errors: List of validation warnings that did not block the save.
        error_message: User-friendly error message if save failed.
    """

    success: bool
    node_id: str | None = None
    node: TreeNode | None = None
    validation_errors: list[str] = field(default_factory=list)
    error_message: str | None = None


class EntityService:
    """Service for entity CRUD operations with proper error handling.

    This service centralizes entity operations and ensures:
    1. Facade is validated before any operation
    2. Validation errors don't block saves (they become warnings)
    3. All errors return user-friendly messages
    4. Comprehensive logging for debugging

    Args:
        session: Database session for async operations.
        dataset: Dataset model containing entity data.
    """

    def __init__(self, session: AsyncSession, dataset: Dataset):
        self._session = session
        self._dataset = dataset
        self._state: AppState | None = None
        self._facade: ProfileFacade | None = None

    @property
    def state(self) -> AppState | None:
        """Get the current AppState, if loaded."""
        return self._state

    @property
    def facade(self) -> ProfileFacade | None:
        """Get the current ProfileFacade, if loaded."""
        return self._facade

    async def ensure_state(self) -> AppState:
        """Ensure AppState is loaded with a valid facade.

        For datasets using custom specs (spec_draft_id), loads the spec from
        the database and creates a ProfileFacade with dependency injection.
        For built-in profiles, creates a standard facade.

        Returns:
            Populated AppState ready for entity operations.

        Raises:
            SpecNotFoundError: If spec_draft_id is set but spec cannot be found.
            FacadeLoadError: If facade creation fails for any reason.
        """
        if self._state is not None:
            return self._state

        # Create state and configure profile/version
        state = AppState()
        state.profile = self._dataset.profile
        state.version = self._dataset.version

        # Load spec from database if dataset uses a custom spec
        if self._dataset.spec_draft_id:
            await self._load_facade_for_draft_spec(state)
        else:
            # Built-in profile - let facade load from filesystem
            try:
                facade = state.get_or_create_facade()
                if facade is None:
                    profile = self._dataset.profile
                    raise FacadeLoadError(
                        f"Failed to create facade for {profile}",
                        user_message=f"Could not load profile '{profile}'. "
                        "Please check that this profile exists.",
                    )
                self._facade = facade
            except Exception as e:
                if isinstance(e, EntityServiceError):
                    raise
                raise FacadeLoadError(
                    f"Failed to create facade: {e}",
                    user_message=f"Could not load profile '{self._dataset.profile}': {e}",
                ) from e

        # Deserialize existing tree data
        if self._dataset.data:
            self._deserialize_tree(state, self._dataset.data)

        self._state = state
        return state

    async def _load_facade_for_draft_spec(self, state: AppState) -> None:
        """Load spec from database and create facade with injected spec.

        Args:
            state: AppState to configure with the loaded facade.

        Raises:
            SpecNotFoundError: If spec draft cannot be found or has no data.
            FacadeLoadError: If ProfileSpec validation or facade creation fails.
        """
        spec_draft = await self._session.get(SpecDraft, self._dataset.spec_draft_id)

        if spec_draft is None:
            raise SpecNotFoundError(
                f"Spec draft {self._dataset.spec_draft_id} not found",
                user_message="The specification for this dataset could not be found. "
                "It may have been deleted.",
            )

        if not spec_draft.spec_data:
            raise SpecNotFoundError(
                f"Spec draft {self._dataset.spec_draft_id} has no spec_data",
                user_message="The specification for this dataset is empty. "
                "Please update the specification before adding entities.",
            )

        # Extract spec data (may be nested under "spec" key for SpecBuilderState format)
        raw_data = spec_draft.spec_data
        if isinstance(raw_data, dict) and "spec" in raw_data:
            raw_data = raw_data["spec"]

        try:
            profile_spec = ProfileSpec.model_validate(raw_data)
        except ValidationError as e:
            logger.error(f"Invalid spec structure for draft {spec_draft.id}: {e}")
            raise FacadeLoadError(
                f"Invalid spec structure: {e}",
                user_message="The specification has an invalid structure. "
                "Please fix the specification in the spec builder.",
            ) from e

        try:
            facade = ProfileFacade(
                profile=self._dataset.profile,
                spec=profile_spec,
            )
            state.facade = facade
            state.profile = facade.profile
            self._facade = facade
            logger.debug(f"Loaded draft spec for dataset {self._dataset.id}")
        except Exception as e:
            logger.error(f"Failed to create facade from draft spec: {e}")
            raise FacadeLoadError(
                f"Failed to create facade: {e}",
                user_message="Could not initialize the profile. "
                "Please check the specification is complete.",
            ) from e

    def _deserialize_tree(self, state: AppState, data: dict[str, Any]) -> None:
        """Deserialize JSON data into AppState entity tree.

        Uses model_construct to skip validation, allowing entities with
        missing required fields to still load and be edited.

        Args:
            state: AppState to populate.
            data: Dictionary loaded from database JSONB.
        """
        if not data or "tree" not in data:
            return

        facade = state.get_or_create_facade()
        if facade is None:
            logger.error("Cannot deserialize tree: facade is None")
            return

        def deserialize_node(
            node_data: dict[str, Any],
            parent_id: str | None = None,
        ) -> TreeNode | None:
            entity_type = node_data.get("entity_type")
            if not entity_type:
                return None

            try:
                helper = getattr(facade, entity_type)
            except AttributeError:
                logger.warning(f"Entity type '{entity_type}' not in facade")
                return None

            instance_data = node_data.get("data", {})
            instance = None

            # Try normal creation first, fall back to model_construct
            try:
                instance = helper.create(**instance_data)
            except Exception as e:
                logger.debug(f"Validation failed for {entity_type}, using model_construct: {e}")
                try:
                    model_class = helper._model
                    instance = model_class.model_construct(**instance_data)
                except Exception as e2:
                    logger.error(f"model_construct failed for {entity_type}: {e2}")

            node = TreeNode(
                id=node_data.get("id", ""),
                entity_type=entity_type,
                instance=instance,
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

    def get_helper(self, entity_type: str) -> Any:
        """Get the helper for an entity type.

        Args:
            entity_type: Name of the entity type.

        Returns:
            Entity helper from the facade.

        Raises:
            EntityTypeNotFoundError: If entity type doesn't exist in the facade.
            FacadeLoadError: If facade hasn't been loaded.
        """
        if self._facade is None:
            raise FacadeLoadError(
                "Facade not loaded",
                user_message="Please call ensure_state() before accessing entity helpers.",
            )

        try:
            return getattr(self._facade, entity_type)
        except AttributeError:
            raise EntityTypeNotFoundError(
                f"Entity type '{entity_type}' not found in facade",
                user_message=f"Unknown entity type: {entity_type}. "
                "Please check the specification includes this entity.",
            )

    async def create_or_update_entity(
        self,
        entity_type: str,
        values: dict[str, Any],
        node_id: str | None = None,
        parent_id: str | None = None,
    ) -> EntitySaveResult:
        """Create or update an entity.

        Always saves the entity even if validation fails. Validation errors
        are returned as warnings in the result.

        Args:
            entity_type: Type of entity to create/update.
            values: Field values for the entity.
            node_id: Existing node ID for updates, None for new entities.
            parent_id: Parent node ID for hierarchical entities.

        Returns:
            EntitySaveResult with success status, node info, and any warnings.
        """
        try:
            state = await self.ensure_state()
        except EntityServiceError as e:
            return EntitySaveResult(
                success=False,
                error_message=e.user_message,
            )

        # Get helper for entity type
        try:
            helper = self.get_helper(entity_type)
        except EntityServiceError as e:
            return EntitySaveResult(
                success=False,
                error_message=e.user_message,
            )

        # Collect validation errors but don't block save
        validation_errors: list[str] = []

        # Try creating with full validation first
        instance = None
        try:
            model_class = helper._model
            instance = model_class(**values)
        except ValidationError as e:
            # Extract validation error messages, but skip "Field required" for
            # nested fields that have SOME data (user may be filling in progressively)
            for error in e.errors():
                field_path = ".".join(str(loc) for loc in error["loc"])
                error_type = error.get("type", "")
                field_name = error["loc"][0] if error["loc"] else ""

                # Skip "missing" errors for nested fields that have partial data
                if error_type == "missing" and field_name in values:
                    field_value = values.get(field_name)
                    # If field has some data (dict with values, non-empty list), skip warning
                    if isinstance(field_value, dict) and field_value:
                        continue
                    if isinstance(field_value, list) and field_value:
                        continue

                validation_errors.append(f"{field_path}: {error['msg']}")

            # Fall back to model_construct (skips validation)
            try:
                instance = model_class.model_construct(**values)
                logger.debug(f"Created {entity_type} with model_construct due to validation errors")
            except Exception as e2:
                logger.error(f"model_construct failed: {e2}")
                return EntitySaveResult(
                    success=False,
                    error_message=f"Failed to create {entity_type}: {e2}",
                )
        except Exception as e:
            logger.error(f"Unexpected error creating {entity_type}: {e}")
            return EntitySaveResult(
                success=False,
                error_message=f"Failed to create {entity_type}: {e}",
            )

        # Update or create node
        if node_id and node_id in state.nodes_by_id:
            # Update existing node
            node = state.nodes_by_id[node_id]
            node.instance = instance
            label = helper.get_label(instance)
            if label:
                node.label = label
        else:
            # Create new node - bypass facade validation if we have validation errors
            if validation_errors:
                # Create TreeNode directly to skip facade's validation
                node = self._create_node_directly(state, entity_type, instance, parent_id, helper)
            else:
                # Use normal flow which validates
                node = state.add_node(entity_type, instance, parent_id=parent_id)
            node_id = node.id

        # Save to database
        try:
            await self._save_state()
        except Exception as e:
            logger.exception(f"Failed to save {entity_type}")
            return EntitySaveResult(
                success=False,
                node_id=node_id,
                node=node,
                validation_errors=validation_errors,
                error_message=f"Failed to save to database: {e}",
            )

        return EntitySaveResult(
            success=True,
            node_id=node_id,
            node=node,
            validation_errors=validation_errors,
        )

    async def delete_entity(self, node_id: str) -> EntitySaveResult:
        """Delete an entity by its node ID.

        Args:
            node_id: ID of the node to delete.

        Returns:
            EntitySaveResult indicating success or failure.
        """
        try:
            state = await self.ensure_state()
        except EntityServiceError as e:
            return EntitySaveResult(
                success=False,
                error_message=e.user_message,
            )

        if node_id not in state.nodes_by_id:
            return EntitySaveResult(
                success=False,
                error_message="Entity not found.",
            )

        # Delete the node
        state.delete_node(node_id)

        # Save to database
        try:
            await self._save_state()
        except Exception as e:
            logger.exception(f"Failed to delete node {node_id}")
            return EntitySaveResult(
                success=False,
                error_message=f"Failed to save deletion: {e}",
            )

        return EntitySaveResult(success=True)

    def _create_node_directly(
        self,
        state: AppState,
        entity_type: str,
        instance: Any,
        parent_id: str | None,
        helper: Any,
    ) -> TreeNode:
        """Create a TreeNode directly without facade validation.

        Used when we need to save entities that have validation errors.
        Bypasses facade.add_entity() which would validate and reject.

        Args:
            state: AppState to add the node to.
            entity_type: Type of entity.
            instance: The entity instance (already created with model_construct).
            parent_id: Optional parent node ID.
            helper: Entity helper for label extraction.

        Returns:
            Created TreeNode.
        """
        import uuid

        # Generate node ID
        node_id = str(uuid.uuid4())

        # Get label from instance
        label = helper.get_label(instance) if helper else f"New {entity_type}"
        if not label:
            label = f"New {entity_type}"

        # Create TreeNode
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

    async def _save_state(self) -> None:
        """Save current state to database."""
        from metaseed_hub.ui.helpers import save_dataset_state

        if self._state is None:
            raise EntityServiceError(
                "Cannot save: state not loaded",
                user_message="Internal error: state not initialized.",
            )

        await save_dataset_state(self._session, self._dataset, self._state)
