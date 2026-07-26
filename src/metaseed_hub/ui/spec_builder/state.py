"""State management for the Spec Builder in Metaseed Hub.

Contains dataclasses for managing spec builder state including the working
specification, current editing context, and change tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from metaseed.specs.schema import ProfileSpec


def spec_to_dict(spec: ProfileSpec) -> dict[str, Any]:
    """Convert a ProfileSpec to a JSON-serializable dict.

    Uses the Pydantic model's own serialization so every schema field is
    preserved (lossless), rather than hand-mapping a subset of fields.
    """
    result: dict[str, Any] = spec.model_dump(mode="json", exclude_none=True)
    return result


def dict_to_spec(data: dict[str, Any]) -> ProfileSpec:
    """Convert a stored dict back to a ProfileSpec.

    Uses Pydantic validation so the round-trip is lossless. Older drafts stored
    with a subset of fields still load because missing fields fall back to their
    schema defaults.
    """
    return ProfileSpec.model_validate(data)


@dataclass
class SpecBuilderState:
    """Server-side state for the Spec Builder.

    Attributes:
        spec: The ProfileSpec being edited, or None if not started.
        editing_entity: Name of entity currently being edited, or None.
        editing_field_idx: Index of field being edited within current entity.
        editing_rule_idx: Index of validation rule being edited.
        template_source: Tuple of (profile, version) if cloned from template.
        has_unsaved_changes: Whether there are unsaved modifications.
    """

    spec: ProfileSpec | None = None
    editing_entity: str | None = None
    editing_field_idx: int | None = None
    editing_rule_idx: int | None = None
    template_source: tuple[str, str] | None = None
    has_unsaved_changes: bool = False

    def reset(self: Self) -> None:
        """Reset all state to initial values."""
        self.spec = None
        self.editing_entity = None
        self.editing_field_idx = None
        self.editing_rule_idx = None
        self.template_source = None
        self.has_unsaved_changes = False

    def reset_to_empty(self: Self, name: str, version: str) -> None:
        """Reset to an empty spec keyed to a draft, clearing editing pointers.

        Unlike ``reset``, the spec is a fresh empty ``ProfileSpec`` (keeping the
        draft's name/version) rather than ``None``. The editing pointers MUST be
        cleared too, or the persisted state keeps referencing an entity/field
        that no longer exists.
        """
        self.spec = ProfileSpec(
            name=name,
            version=version,
            root_entity="",
            entities={},
            validation_rules=[],
        )
        self.editing_entity = None
        self.editing_field_idx = None
        self.editing_rule_idx = None

    def mark_changed(self: Self) -> None:
        """Mark that unsaved changes exist."""
        self.has_unsaved_changes = True

    def mark_saved(self: Self) -> None:
        """Mark that all changes have been saved."""
        self.has_unsaved_changes = False

    def is_active(self: Self) -> bool:
        """Check if a spec is currently being edited."""
        return self.spec is not None

    def get_entity_names(self: Self) -> list[str]:
        """Get list of entity names in the spec."""
        if self.spec is None:
            return []
        return list(self.spec.entities.keys())

    def get_current_entity_field_count(self: Self) -> int:
        """Get field count for the currently editing entity."""
        if self.spec is None or self.editing_entity is None:
            return 0
        entity = self.spec.entities.get(self.editing_entity)
        if entity is None:
            return 0
        return len(entity.fields)

    def to_dict(self: Self) -> dict[str, Any]:
        """Serialize state to a dict for database storage."""
        return {
            "spec": spec_to_dict(self.spec) if self.spec else None,
            "editing_entity": self.editing_entity,
            "editing_field_idx": self.editing_field_idx,
            "editing_rule_idx": self.editing_rule_idx,
            "template_source": list(self.template_source) if self.template_source else None,
            "has_unsaved_changes": self.has_unsaved_changes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Load state from a dict (from database)."""
        spec = None
        if data.get("spec"):
            spec = dict_to_spec(data["spec"])
        template_source = None
        if data.get("template_source"):
            template_source = tuple(data["template_source"])
        return cls(
            spec=spec,
            editing_entity=data.get("editing_entity"),
            editing_field_idx=data.get("editing_field_idx"),
            editing_rule_idx=data.get("editing_rule_idx"),
            template_source=template_source,
            has_unsaved_changes=data.get("has_unsaved_changes", False),
        )
