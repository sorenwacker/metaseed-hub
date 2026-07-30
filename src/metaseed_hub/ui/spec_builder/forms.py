"""Form data models for spec builder.

Provides dataclasses that encapsulate form parameters for cleaner route signatures.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaseed.specs.schema import Constraints, FieldType


def _parse_int(raw: str, label: str) -> int | None:
    """Parse an optional whole-number constraint, or raise a clear ValueError."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{label} must be a whole number") from None


def _parse_float(raw: str, label: str) -> float | None:
    """Parse an optional numeric constraint, or raise a clear ValueError."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{label} must be a number") from None


@dataclass
class FieldFormData:
    """Form data for creating or updating a field."""

    name: str
    field_type: str = "string"
    required: bool = False
    description: str = ""
    ontology_term: str = ""
    ontologies: str = ""
    codename: str = ""
    items: str = ""
    parent_ref: str = ""
    pattern: str = ""
    min_length: str = ""
    max_length: str = ""
    minimum: str = ""
    maximum: str = ""
    min_items: str = ""
    max_items: str = ""
    enum_values: str = ""
    unique_within: str = ""
    reference: str = ""

    def get_field_type(self) -> FieldType:
        """Get the FieldType enum value."""
        return FieldType(self.field_type)

    def get_constraints(self) -> Constraints | None:
        """Build Constraints object if any constraint values are provided."""
        has_constraints = any(
            [
                self.pattern,
                self.min_length,
                self.max_length,
                self.minimum,
                self.maximum,
                self.min_items,
                self.max_items,
                self.enum_values,
            ]
        )
        if not has_constraints:
            return None

        return Constraints(
            pattern=self.pattern.strip() or None,
            min_length=_parse_int(self.min_length, "Minimum length"),
            max_length=_parse_int(self.max_length, "Maximum length"),
            minimum=_parse_float(self.minimum, "Minimum"),
            maximum=_parse_float(self.maximum, "Maximum"),
            min_items=_parse_int(self.min_items, "Minimum items"),
            max_items=_parse_int(self.max_items, "Maximum items"),
            enum=[v.strip() for v in self.enum_values.split("\n") if v.strip()]
            if self.enum_values.strip()
            else None,
        )


@dataclass
class ValidationRuleFormData:
    """Form data for creating or updating a validation rule."""

    name: str
    description: str = ""
    applies_to: str = "all"
    field_name: str = ""  # renamed from 'field' to avoid keyword
    condition: str = ""
    pattern: str = ""
    minimum: str = ""
    maximum: str = ""
    enum_values: str = ""
    reference: str = ""
    unique_within: str = ""
    min_items: str = ""
    max_items: str = ""

    def get_applies_to(self) -> str | list[str]:
        """Parse applies_to into string or list."""
        applies_to = self.applies_to.strip()
        if applies_to == "all":
            return "all"
        entities = [e.strip() for e in applies_to.split(",") if e.strip()]
        if len(entities) == 1:
            return entities[0]
        return entities

    def get_enum(self) -> list[str] | None:
        """Parse enum values from newline-separated string."""
        if not self.enum_values.strip():
            return None
        return [v.strip() for v in self.enum_values.split("\n") if v.strip()]

    def get_minimum(self) -> float | None:
        """Parse the minimum bound, or raise ValueError with a clear message."""
        return _parse_float(self.minimum, "Minimum")

    def get_maximum(self) -> float | None:
        """Parse the maximum bound, or raise ValueError with a clear message."""
        return _parse_float(self.maximum, "Maximum")

    def get_min_items(self) -> int | None:
        """Parse the minimum item count, or raise ValueError with a clear message."""
        return _parse_int(self.min_items, "Minimum items")

    def get_max_items(self) -> int | None:
        """Parse the maximum item count, or raise ValueError with a clear message."""
        return _parse_int(self.max_items, "Maximum items")
