"""SQLAlchemy model mixins for common functionality."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamps to models.

    Both timestamps are timezone-aware (TIMESTAMPTZ in PostgreSQL).
    created_at is set once on insert. updated_at is updated on every change.
    """

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        """Timestamp when the record was created."""
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:  # noqa: N805
        """Timestamp when the record was last updated."""
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )


class SoftDeleteMixin:
    """Mixin that adds soft delete functionality to models.

    Instead of hard deleting records, sets deleted_at to the current timestamp.
    Provides is_deleted property and soft_delete() method.
    """

    @declared_attr
    def deleted_at(cls) -> Mapped[datetime | None]:  # noqa: N805
        """Timestamp when the record was soft deleted, or None if active."""
        return mapped_column(
            DateTime(timezone=True),
            nullable=True,
            default=None,
        )

    @property
    def is_deleted(self) -> bool:
        """Return True if this record has been soft deleted."""
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Mark this record as soft deleted by setting deleted_at."""
        self.deleted_at = datetime.now(UTC)

    def restore(self) -> None:
        """Restore a soft deleted record by clearing deleted_at."""
        self.deleted_at = None


__all__ = ["SoftDeleteMixin", "TimestampMixin"]
