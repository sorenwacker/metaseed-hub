"""SQLAlchemy models for metaseed-hub.

A package split by aggregate; every name is re-exported here so call sites and
Alembic keep importing ``from metaseed_hub.models import X``. Importing this
module imports every model module, which is what lets SQLAlchemy resolve the
string-name relationships and Alembic autogenerate see the full schema.
"""

from .base import Base, _enum_values
from .comments import (
    Comment,
    CommentReaction,
    Note,
    ReactionType,
    SpecComment,
    SpecCommentReaction,
)
from .datasets import Dataset, DatasetMember, DatasetRole, DatasetVersion
from .identity import Team, TeamMembership, TeamRole, Tenant, User
from .mixins import SoftDeleteMixin, TimestampMixin
from .operations import ApiToken, ErrorEvent, FeatureGrant
from .specs import (
    Spec,
    SpecDraft,
    SpecDraftMember,
    SpecDraftRole,
    SpecMember,
    SpecRole,
    SpecStatus,
)

__all__ = [
    "ApiToken",
    "Base",
    "Comment",
    "CommentReaction",
    "Dataset",
    "DatasetMember",
    "DatasetRole",
    "DatasetVersion",
    "ErrorEvent",
    "FeatureGrant",
    "Note",
    "ReactionType",
    "SoftDeleteMixin",
    "Spec",
    "SpecComment",
    "SpecCommentReaction",
    "SpecDraft",
    "SpecDraftMember",
    "SpecDraftRole",
    "SpecMember",
    "SpecRole",
    "SpecStatus",
    "Team",
    "TeamMembership",
    "TeamRole",
    "Tenant",
    "TimestampMixin",
    "User",
    "_enum_values",
]
