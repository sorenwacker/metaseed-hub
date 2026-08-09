"""Notes, comments and reactions on datasets and specs."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _enum_values

if TYPE_CHECKING:
    # Only for annotations: the real links are resolved by name through
    # SQLAlchemy's registry, so no module imports another at runtime and
    # there is nothing to form a cycle.
    from .datasets import Dataset
    from .identity import User
    from .specs import SpecDraft

from .mixins import TimestampMixin


class Note(TimestampMixin, Base):
    """Notes attached to entities within a dataset."""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_dataset_entity", "dataset_id", "entity_type", "entity_id"),)

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
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="notes")
    user: Mapped["User"] = relationship("User", back_populates="notes")


class Comment(TimestampMixin, Base):
    """Threaded comments on datasets (Slack-style)."""

    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_dataset_id", "dataset_id"),
        Index("ix_comments_parent_id", "parent_id"),
        Index("ix_comments_created_at", "created_at"),
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
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="comments")
    user: Mapped["User"] = relationship("User", back_populates="comments")
    parent: Mapped["Comment | None"] = relationship(
        "Comment", remote_side="Comment.id", back_populates="replies"
    )
    # passive_deletes: parent_id is ON DELETE CASCADE, so deleting a comment
    # must let the database remove its replies. Without this the ORM nulls
    # parent_id first, silently promoting replies to top-level comments.
    replies: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="parent", order_by="Comment.created_at", passive_deletes=True
    )
    reactions: Mapped[list["CommentReaction"]] = relationship(
        "CommentReaction", back_populates="comment", cascade="all, delete-orphan"
    )


class ReactionType(StrEnum):
    """Types of reactions on comments."""

    LIKE = "like"
    DISLIKE = "dislike"


class CommentReaction(Base):
    """User reactions (like/dislike) on comments."""

    __tablename__ = "comment_reactions"
    __table_args__ = (Index("ix_comment_reactions_comment_id", "comment_id"),)

    comment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("comments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reaction: Mapped[ReactionType] = mapped_column(
        Enum(ReactionType, name="reactiontype", values_callable=_enum_values),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    comment: Mapped["Comment"] = relationship("Comment", back_populates="reactions")
    user: Mapped["User"] = relationship("User", back_populates="comment_reactions")


class SpecComment(TimestampMixin, Base):
    """Threaded comments on spec drafts."""

    __tablename__ = "spec_comments"
    __table_args__ = (
        Index("ix_spec_comments_spec_draft_id", "spec_draft_id"),
        Index("ix_spec_comments_parent_id", "parent_id"),
        Index("ix_spec_comments_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    spec_draft_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    spec_draft: Mapped["SpecDraft"] = relationship("SpecDraft", back_populates="comments")
    user: Mapped["User"] = relationship("User", back_populates="spec_comments")
    parent: Mapped["SpecComment | None"] = relationship(
        "SpecComment", remote_side="SpecComment.id", back_populates="replies"
    )
    # passive_deletes: same reasoning as Comment.replies — the FK cascade
    # removes replies, and the ORM must not null parent_id first.
    replies: Mapped[list["SpecComment"]] = relationship(
        "SpecComment",
        back_populates="parent",
        order_by="SpecComment.created_at",
        passive_deletes=True,
    )
    reactions: Mapped[list["SpecCommentReaction"]] = relationship(
        "SpecCommentReaction", back_populates="comment", cascade="all, delete-orphan"
    )


class SpecCommentReaction(Base):
    """User reactions (like/dislike) on spec comments."""

    __tablename__ = "spec_comment_reactions"
    __table_args__ = (Index("ix_spec_comment_reactions_comment_id", "comment_id"),)

    comment_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("spec_comments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reaction: Mapped[ReactionType] = mapped_column(
        Enum(
            ReactionType,
            name="reactiontype",
            create_type=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    comment: Mapped["SpecComment"] = relationship("SpecComment", back_populates="reactions")
    user: Mapped["User"] = relationship("User", back_populates="spec_comment_reactions")
