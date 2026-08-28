"""SQLAlchemy models for metaseed-hub.

A package split by aggregate; every name is re-exported here so call sites and
Alembic keep importing ``from metaseed_hub.models import X``. Importing this
module imports every model module, which is what lets SQLAlchemy resolve the
string-name relationships and Alembic autogenerate see the full schema.
"""

from metaseed_hub.sharing import Role

from .base import Base, _enum_values
from .comments import (
    Comment,
    CommentReaction,
    ReactionType,
    SpecComment,
    SpecCommentReaction,
)
from .datasets import Dataset, DatasetMember, DatasetVersion
from .identity import Tenant, User
from .mixins import SoftDeleteMixin, TimestampMixin
from .operations import ApiToken, ErrorEvent, SeekConnection
from .specs import (
    Spec,
    SpecDraft,
    SpecDraftMember,
    SpecMember,
    SpecStatus,
)

__all__ = [
    "Role",
    "ApiToken",
    "Base",
    "Comment",
    "CommentReaction",
    "Dataset",
    "DatasetMember",
    "DatasetVersion",
    "ErrorEvent",
    "ReactionType",
    "SeekConnection",
    "SoftDeleteMixin",
    "Spec",
    "SpecComment",
    "SpecCommentReaction",
    "SpecDraft",
    "SpecDraftMember",
    "SpecMember",
    "SpecStatus",
    "Tenant",
    "TimestampMixin",
    "User",
    "_enum_values",
]
