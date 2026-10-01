"""SQLAlchemy model mixins for common functionality."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from metaseed_hub.sharing import Role

from .base import _enum_values


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


class MemberMixin:
    """The columns every membership table shares; the subclass names the resource.

    A membership is one user with one role on one shared thing. The user and
    the role are the same columns on datasets, specifications and drafts; the
    subclass adds the resource key and the relationships.
    """

    @declared_attr
    def user_id(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(
            UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        )

    @declared_attr
    def role(cls) -> Mapped[Role]:  # noqa: N805
        return mapped_column(
            Enum(Role, name="memberrole", values_callable=_enum_values),
            nullable=False,
            default=Role.VIEWER,
        )

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


__all__ = ["MemberMixin", "SoftDeleteMixin", "TimestampMixin"]
