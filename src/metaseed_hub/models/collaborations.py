"""What the hub records about SRAM collaborations.

Group membership is stated by the identity provider at sign-in and nowhere
else, so :class:`GroupMembership` is a snapshot of that statement per user,
replaced on every sign-in. The grant tables give a whole collaboration, or one
of its groups, a role on a shared thing; one table per kind so the database
removes a grant with the thing it is on, exactly as the member tables do.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from metaseed_hub.sharing import Role

from .base import Base, _enum_values

#: Long enough for a SRAM group URN, which nests organisation, collaboration
#: and group under a fixed prefix.
URN_LENGTH = 512


class GroupMembership(Base):
    """One SRAM group URN the identity provider reported for a user.

    When it was reported is not here: an empty reading has no rows to carry a
    time, and that reading is exactly the one worth telling apart from never
    having asked. ``User.memberships_read_at`` holds it.
    """

    __tablename__ = "group_memberships"
    __table_args__ = (Index("ix_group_memberships_urn", "urn"),)

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    urn: Mapped[str] = mapped_column(String(URN_LENGTH), primary_key=True)


class CollaborationGrantMixin:
    """The columns every grant table shares; the subclass names the resource."""

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    urn: Mapped[str] = mapped_column(String(URN_LENGTH), nullable=False)

    @declared_attr
    def role(cls) -> Mapped[Role]:  # noqa: N805
        return mapped_column(
            Enum(Role, name="memberrole", values_callable=_enum_values), nullable=False
        )

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:  # noqa: N805
        return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DatasetCollaborationGrant(CollaborationGrantMixin, Base):
    """A collaboration's role on a dataset."""

    __tablename__ = "dataset_collaboration_grants"
    __table_args__ = (
        UniqueConstraint("dataset_id", "urn", name="uq_dataset_collaboration_grants"),
    )

    dataset_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )


class SpecDraftCollaborationGrant(CollaborationGrantMixin, Base):
    """A collaboration's role on a specification draft."""

    __tablename__ = "spec_draft_collaboration_grants"
    __table_args__ = (
        UniqueConstraint("spec_draft_id", "urn", name="uq_spec_draft_collaboration_grants"),
    )

    spec_draft_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("spec_drafts.id", ondelete="CASCADE"), nullable=False
    )


class SpecCollaborationGrant(CollaborationGrantMixin, Base):
    """A collaboration's role on a published specification."""

    __tablename__ = "spec_collaboration_grants"
    __table_args__ = (UniqueConstraint("spec_id", "urn", name="uq_spec_collaboration_grants"),)

    spec_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("specs.id", ondelete="CASCADE"), nullable=False
    )
