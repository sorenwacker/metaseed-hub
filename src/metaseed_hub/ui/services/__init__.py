"""Service layer for Hub UI entity operations."""

from .entity_service import EntitySaveResult, EntityService
from .exceptions import (
    EntityServiceError,
    EntityTypeNotFoundError,
    FacadeLoadError,
    SpecNotFoundError,
)

__all__ = [
    "EntitySaveResult",
    "EntityService",
    "EntityServiceError",
    "EntityTypeNotFoundError",
    "FacadeLoadError",
    "SpecNotFoundError",
]
