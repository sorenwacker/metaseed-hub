"""Entity service for Hub UI operations.

This module provides a service layer for entity CRUD operations that:
- Guarantees client is valid before operations
- Always saves entities (validation errors become warnings, not blockers)
- Returns user-friendly error messages instead of 500 errors
- Provides comprehensive logging

Uses MetaseedClient public API exclusively - no internal access needed.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from metaseed import MetaseedClient, ProfileNotFoundError
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
    node: Any | None = None  # TreeNode, kept as Any to avoid circular import
    validation_errors: list[str] = field(default_factory=list)
    error_message: str | None = None


class EntityService:
    """Service for entity CRUD operations with proper error handling.

    This service centralizes entity operations and ensures:
    1. MetaseedClient is validated before any operation
    2. Validation errors don't block saves (they become warnings)
    3. All errors return user-friendly messages
    4. Comprehensive logging for debugging

    Uses MetaseedClient public API exclusively for all operations.

    Args:
        session: Database session for async operations.
        dataset: Dataset model containing entity data.
    """

    def __init__(self, session: AsyncSession, dataset: Dataset):
        self._session = session
        self._dataset = dataset
        self._client: MetaseedClient | None = None
        self._state: Any | None = None  # AppState, imported lazily

    @property
    def client(self) -> MetaseedClient | None:
        """Get the MetaseedClient, if loaded."""
        return self._client

    @property
    def state(self) -> Any | None:
        """Get the current AppState, if loaded."""
        return self._state

    @property
    def facade(self) -> Any | None:
        """Get the ProfileFacade from the client, if loaded.

        Provided for backward compatibility with code that needs facade access.
        """
        return self._client.facade if self._client else None

    async def ensure_state(self) -> Any:
        """Ensure AppState is loaded with a valid client.

        For datasets using custom specs (spec_draft_id), loads the spec from
        the database and creates a MetaseedClient with from_spec().
        For built-in profiles, creates a standard client.

        Returns:
            Populated AppState ready for entity operations.

        Raises:
            SpecNotFoundError: If spec_draft_id is set but spec cannot be found.
            FacadeLoadError: If client creation fails for any reason.
        """
        from metaseed.ui.state import AppState

        if self._state is not None:
            return self._state

        # Create state and configure profile/version
        state = AppState()
        state.profile = self._dataset.profile
        state.version = self._dataset.version

        # Load client (from custom spec or built-in profile)
        if self._dataset.spec_draft_id:
            await self._load_client_for_draft_spec()
        else:
            self._load_builtin_client()

        # Inject facade into state for compatibility with existing code
        if self._client:
            state.facade = self._client.facade

        # Deserialize existing tree data using client.load()
        if self._dataset.data:
            self._load_tree_data(self._dataset.data)
            # Invalidate AppState cache to rebuild from client
            state.invalidate_cache()

        self._state = state
        return state

    def _load_builtin_client(self) -> None:
        """Load MetaseedClient for a built-in profile.

        Raises:
            FacadeLoadError: If profile not found or client creation fails.
        """
        try:
            self._client = MetaseedClient(
                self._dataset.profile,
                self._dataset.version,
            )
            logger.debug(f"Loaded built-in profile: {self._dataset.profile}")
        except ProfileNotFoundError as e:
            raise FacadeLoadError(
                f"Profile not found: {e}",
                user_message=f"Could not load profile '{self._dataset.profile}'. "
                "Please check that this profile exists.",
            ) from e
        except Exception as e:
            raise FacadeLoadError(
                f"Failed to create client: {e}",
                user_message=f"Could not load profile '{self._dataset.profile}': {e}",
            ) from e

    async def _load_client_for_draft_spec(self) -> None:
        """Load MetaseedClient from database spec draft.

        Uses MetaseedClient.from_spec() for clean API access.

        Raises:
            SpecNotFoundError: If spec draft cannot be found or has no data.
            FacadeLoadError: If spec validation or client creation fails.
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
            self._client = MetaseedClient.from_spec(raw_data)
            logger.debug(f"Loaded draft spec for dataset {self._dataset.id}")
        except Exception as e:
            logger.error(f"Failed to create client from draft spec: {e}")
            raise FacadeLoadError(
                f"Failed to create client: {e}",
                user_message="Could not initialize the profile. "
                "Please check the specification is complete.",
            ) from e

    def _load_tree_data(self, data: dict[str, Any]) -> None:
        """Load tree data into client using public API.

        Uses client.load() which auto-detects tree vs flat format.

        Args:
            data: Dictionary loaded from database JSONB.
        """
        if not data:
            return

        if not self._client:
            logger.error("Cannot load tree: client is None")
            return

        try:
            # client.load() auto-detects tree vs flat format
            count = self._client.load(data)
            logger.debug(f"Loaded {count} entities from database")
        except Exception as e:
            logger.error(f"Failed to load tree data: {e}")

    def get_helper(self, entity_type: str) -> Any:
        """Get the helper for an entity type.

        Args:
            entity_type: Name of the entity type.

        Returns:
            Entity helper from the facade.

        Raises:
            EntityTypeNotFoundError: If entity type doesn't exist.
            FacadeLoadError: If client hasn't been loaded.
        """
        if self._client is None:
            raise FacadeLoadError(
                "Client not loaded",
                user_message="Please call ensure_state() before accessing entity helpers.",
            )

        # Check if entity type exists using public API
        if entity_type not in self._client.list_entity_types():
            raise EntityTypeNotFoundError(
                f"Entity type '{entity_type}' not found",
                user_message=f"Unknown entity type: {entity_type}. "
                "Please check the specification includes this entity.",
            )

        # Return helper for backward compatibility (still needs facade access)
        return getattr(self._client.facade, entity_type)

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

        Uses MetaseedClient public API with skip_validation=True.

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

        # Client is guaranteed to be set after ensure_state()
        assert self._client is not None

        # Validate entity type exists using public API
        if entity_type not in self._client.list_entity_types():
            return EntitySaveResult(
                success=False,
                error_message=f"Unknown entity type: {entity_type}. "
                "Please check the specification includes this entity.",
            )

        # Collect validation warnings using client.validate_entity() after creation
        validation_errors: list[str] = []

        # Update or create using MetaseedClient public API
        if node_id and node_id in state.nodes_by_id:
            # Update existing entity
            try:
                entity = self._client.update_entity(node_id, values, skip_validation=True)
                # Invalidate AppState cache to rebuild from client
                state.invalidate_cache()
                node = state.nodes_by_id.get(node_id)

                # Get validation warnings
                validation_errors = self._get_validation_warnings(entity_type, values)
            except Exception as e:
                logger.error(f"Failed to update {entity_type}: {e}")
                return EntitySaveResult(
                    success=False,
                    error_message=f"Failed to update {entity_type}: {e}",
                )
        else:
            # Create new entity
            try:
                entity = self._client.create_entity(
                    entity_type, values, parent_id=parent_id, skip_validation=True
                )
                # Invalidate AppState cache to rebuild with new entity
                state.invalidate_cache()
                node_id = entity.id
                node = state.nodes_by_id.get(node_id)

                # Get validation warnings
                validation_errors = self._get_validation_warnings(entity_type, values)
            except Exception as e:
                logger.error(f"Failed to create {entity_type}: {e}")
                return EntitySaveResult(
                    success=False,
                    error_message=f"Failed to create {entity_type}: {e}",
                )

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

    def _get_validation_warnings(self, entity_type: str, values: dict[str, Any]) -> list[str]:
        """Get validation warnings for entity data without blocking.

        Attempts to create entity with full validation to capture errors.

        Args:
            entity_type: Type of entity.
            values: Field values to validate.

        Returns:
            List of validation warning messages.
        """
        warnings: list[str] = []

        if not self._client:
            return warnings

        # Try creating with validation to capture errors
        try:
            # Use public API to get model class
            model_class = self._client.get_model(entity_type)
            model_class(**values)
        except Exception as e:
            # Extract validation error messages
            if hasattr(e, "errors"):
                for error in e.errors():
                    field_path = ".".join(str(loc) for loc in error["loc"])
                    error_type = error.get("type", "")
                    field_name = error["loc"][0] if error["loc"] else ""

                    # Skip "missing" errors for nested fields with partial data
                    if error_type == "missing" and field_name in values:
                        field_value = values.get(field_name)
                        if isinstance(field_value, dict) and field_value:
                            continue
                        if isinstance(field_value, list) and field_value:
                            continue

                    warnings.append(f"{field_path}: {error['msg']}")

        return warnings

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

        # Client is guaranteed to be set after ensure_state()
        assert self._client is not None

        # Delete using MetaseedClient public API
        try:
            self._client.delete_entity(node_id)
            # Invalidate AppState cache to rebuild without deleted entity
            state.invalidate_cache()
        except Exception as e:
            logger.error(f"Failed to delete entity: {e}")
            return EntitySaveResult(
                success=False,
                error_message="Failed to delete entity.",
            )

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

    async def _save_state(self) -> None:
        """Save current state to database using client.serialize()."""
        if self._client is None:
            raise EntityServiceError(
                "Cannot save: client not loaded",
                user_message="Internal error: client not initialized.",
            )

        # Use client.serialize(format='tree') for database storage
        tree_data = self._client.serialize(format="tree")

        # Update dataset
        self._dataset.data = tree_data
        self._session.add(self._dataset)
        await self._session.commit()
