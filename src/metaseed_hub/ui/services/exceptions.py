"""Custom exceptions for entity service operations.

These exceptions provide user-friendly error messages for common failure scenarios
when working with entities and facades.
"""


class EntityServiceError(Exception):
    """Base exception with user-friendly message.

    All entity service errors inherit from this class. Each error carries both
    a technical message (for logging) and a user-friendly message (for display).

    Args:
        message: Technical error message for logging.
        user_message: User-friendly message for display. Defaults to message if not provided.
    """

    def __init__(self, message: str, user_message: str | None = None):
        super().__init__(message)
        self.message = message
        self.user_message = user_message or message


class FacadeLoadError(EntityServiceError):
    """Failed to load or create the ProfileFacade.

    Raised when the facade cannot be created, either because the spec
    cannot be loaded or the facade initialization fails.
    """

    pass


class DatasetDataLoadError(EntityServiceError):
    """Stored dataset entity data could not be deserialized.

    Raised when the dataset's stored tree data cannot be loaded into the
    client. Load failures must never be treated as an empty dataset: a
    subsequent save would overwrite the stored entity tree.
    """

    pass


class EntityTypeNotFoundError(EntityServiceError):
    """Entity type not found in the facade.

    Raised when trying to access an entity type that doesn't exist
    in the current profile's schema.
    """

    pass
