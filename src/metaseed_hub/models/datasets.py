"""Datasets, their versions and who may touch them."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from metaseed_hub.sharing import Role

from .base import Base, _enum_values

if TYPE_CHECKING:
    # Only for annotations: the real links are resolved by name through
    # SQLAlchemy's registry, so no module imports another at runtime and
    # there is nothing to form a cycle.
    from .comments import Comment
    from .identity import Tenant, User
    from .specs import Spec, SpecDraft

from .mixins import SoftDeleteMixin, TimestampMixin


class Dataset(TimestampMixin, SoftDeleteMixin, Base):
    """Dataset containing a single spec instance with entity data."""

    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_datasets_tenant_name"),
        Index("ix_datasets_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    spec_draft_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_drafts.id", ondelete="SET NULL"),
        nullable=True,
    )
    spec_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        # SET NULL, not CASCADE: withdrawing a specification must not delete the
        # datasets built on it.
        ForeignKey("specs.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    profile: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    # server_default mirrors what the table has carried since the rename from
    # projects; declaring it keeps the model and the migrations comparable.
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="datasets")
    spec_draft: Mapped["SpecDraft | None"] = relationship("SpecDraft")
    spec: Mapped["Spec | None"] = relationship("Spec")
    members: Mapped[list["DatasetMember"]] = relationship("DatasetMember", back_populates="dataset")
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="dataset", order_by="Comment.created_at"
    )
    versions: Mapped[list["DatasetVersion"]] = relationship(
        "DatasetVersion", back_populates="dataset", order_by="DatasetVersion.version_number.desc()"
    )


class DatasetVersion(TimestampMixin, Base):
    """Snapshot of dataset data at a point in time."""

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dataset_versions_number"),
        Index("ix_dataset_versions_dataset_id", "dataset_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="versions")
    created_by: Mapped["User | None"] = relationship("User")


#: Roles are one vocabulary across every shared thing; see
#: :mod:`metaseed_hub.sharing`. The alias keeps the old name readable at call
#: sites that talk about datasets specifically.


class DatasetMember(Base):
    """User membership in a dataset with role-based access."""

    __tablename__ = "dataset_members"
    __table_args__ = (
        Index("ix_dataset_members_dataset_id", "dataset_id"),
        Index("ix_dataset_members_user_id", "user_id"),
    )

    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="memberrole", values_callable=_enum_values),
        nullable=False,
        default=Role.VIEWER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="dataset_memberships")
