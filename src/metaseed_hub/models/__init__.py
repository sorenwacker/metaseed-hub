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
    ReactionType,
    SpecComment,
    SpecCommentReaction,
)
from .datasets import Dataset, DatasetMember, DatasetRole, DatasetVersion
from .identity import Tenant, User
from .mixins import SoftDeleteMixin, TimestampMixin
from .operations import ApiToken, ErrorEvent, FeatureGrant, SeekConnection
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
    "ReactionType",
    "SeekConnection",
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
    "Tenant",
    "TimestampMixin",
    "User",
    "_enum_values",
]
