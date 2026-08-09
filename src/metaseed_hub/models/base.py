"""Declarative base and helpers every model shares."""

from enum import StrEnum
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


def _enum_values(enum: type[StrEnum]) -> list[str]:
    """Return an enum's member values, for storing enums by value not name.

    Passed as ``Enum(..., values_callable=_enum_values)`` so every persisted enum
    column stores the lowercase member value (e.g. ``"owner"``) uniformly, rather
    than roles/status storing the uppercase member name while reactions store the
    value. See migration ``260726_enum_store_by_value``.
    """
    return [member.value for member in enum]
