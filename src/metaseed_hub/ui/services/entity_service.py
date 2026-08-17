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
from typing import TYPE_CHECKING, Any

from metaseed import MetaseedClient
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from metaseed_hub.auth import TokenUser

from metaseed_hub.models import Dataset
from metaseed_hub.ui.helpers import save_dataset_state

from .exceptions import (
    EntityServiceError,
    EntityTypeNotFoundError,
    FacadeLoadError,
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

    def __init__(
        self,
        session: AsyncSession,
        dataset: Dataset,
        user: "TokenUser | None" = None,
    ):
        self._session = session
        self._dataset = dataset
        self._user = user
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

        For datasets using draft specs (spec_draft_id) or published specs
        (spec_id), loads the spec from the database and creates a
        MetaseedClient with from_spec(). For built-in profiles, creates a
        standard client.

        Returns:
            Populated AppState ready for entity operations.

        Raises:
            DatasetDataLoadError: If no client could be built for the dataset —
                a missing or empty spec included — or its stored entity data
                cannot be deserialized.
            HTTPException: 409 naming the nodes that did not load. This is not
                an EntityServiceError, so a caller catching only that will let
                it propagate, which is intended: the refusal carries the way
                through for the user.
        """
        from metaseed.api.client import MetaseedClient

        from metaseed_hub.ui.helpers.dataset_state import (
            ensure_dataset_facade_for_write,
        )

        if self._state is not None:
            return self._state

        # THE single load path (its docstring's words), write-flavoured: every
        # entity route mutates and then saves, so an unplaceable stored node
        # gets the same 409 refusal every cell edit gets, instead of this
        # service's former strict load failing with a different error. The
        # duplicated draft/published/builtin resolution and envelope
        # unwrapping lived here too, and fixes had to land twice.
        state = await ensure_dataset_facade_for_write(self._dataset, self._session)
        self._client = MetaseedClient.from_facade(state.facade)
        self._state = state
        return state

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
                A node_id that no longer exists fails the save instead of
                creating a duplicate entity.
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

        # A stale node_id (entity deleted in another tab or by another user)
        # must fail instead of silently creating a duplicate entity.
        if node_id and node_id not in state.nodes_by_id:
            return EntitySaveResult(
                success=False,
                error_message="Entity not found. It may have been deleted.",
            )

        # Collect validation warnings using client.validate_entity() after creation
        validation_errors: list[str] = []

        # Update or create using MetaseedClient public API
        if node_id:
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

        A missing required field raises nothing (metaseed reports requiredness
        rather than enforcing it), so those are looked for directly. Everything
        else is still learned by attempting a fully validated construction.

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
            for name in model_class.spec_required_fields():
                if values.get(name) in (None, "", [], {}):
                    warnings.append(f"{name}: Field required")
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

    async def validate_entity(
        self,
        entity_type: str,
        values: dict[str, Any],
    ) -> list[str]:
        """Validate entity data without saving.

        Uses the same validation as create/update but only returns errors.

        Args:
            entity_type: Type of entity to validate.
            values: Field values to validate.

        Returns:
            List of validation error messages, empty if valid.
        """
        try:
            await self.ensure_state()
        except EntityServiceError as e:
            return [e.user_message]

        # Client is guaranteed to be set after ensure_state()
        assert self._client is not None

        # Validate entity type exists using public API
        if entity_type not in self._client.list_entity_types():
            return [f"Unknown entity type: {entity_type}"]

        return self._get_validation_warnings(entity_type, values)

    async def _save_state(self) -> None:
        """Persist the current entity tree through the shared dataset-state helper.

        Delegates serialization, version creation, and persistence to
        save_dataset_state so the versioning logic exists in one place.

        Raises:
            EntityServiceError: If called before ensure_state() loaded the state.
        """
        if self._state is None:
            raise EntityServiceError(
                "Cannot save: state not loaded",
                user_message="Internal error: state not initialized.",
            )

        await save_dataset_state(self._session, self._dataset, self._state, self._user)
