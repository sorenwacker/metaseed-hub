"""State management for the Spec Builder in Metaseed Hub.

Contains dataclasses for managing spec builder state including the working
specification, current editing context, and change tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Self

from metaseed.specs.schema import (
    Constraints,
    EntityDefSpec,
    FieldSpec,
    FieldType,
    ProfileSpec,
    ValidationRuleSpec,
)


def spec_to_dict(spec: ProfileSpec) -> dict[str, Any]:
    """Convert ProfileSpec to a JSON-serializable dict."""
    entities = {}
    for name, entity in spec.entities.items():
        fields = []
        for f in entity.fields:
            field_dict: dict[str, Any] = {
                "name": f.name,
                "type": f.type.value,
                "required": f.required,
            }
            if f.description:
                field_dict["description"] = f.description
            if f.items:
                field_dict["items"] = f.items
            if f.ontology_term:
                field_dict["ontology_term"] = f.ontology_term
            if f.constraints:
                constraints: dict[str, Any] = {}
                if f.constraints.min_length is not None:
                    constraints["min_length"] = f.constraints.min_length
                if f.constraints.max_length is not None:
                    constraints["max_length"] = f.constraints.max_length
                if f.constraints.minimum is not None:
                    constraints["minimum"] = f.constraints.minimum
                if f.constraints.maximum is not None:
                    constraints["maximum"] = f.constraints.maximum
                if f.constraints.pattern:
                    constraints["pattern"] = f.constraints.pattern
                if f.constraints.enum:
                    constraints["enum"] = f.constraints.enum
                if constraints:
                    field_dict["constraints"] = constraints
            fields.append(field_dict)
        entities[name] = {
            "ontology_term": entity.ontology_term,
            "description": entity.description,
            "fields": fields,
        }

    rules = []
    for rule in spec.validation_rules:
        rules.append(
            {
                "name": rule.name,
                "description": rule.description,
                "expression": rule.expression,
                "level": rule.level,
            }
        )

    return {
        "name": spec.name,
        "version": spec.version,
        "display_name": spec.display_name,
        "description": spec.description,
        "ontology": spec.ontology,
        "root_entity": spec.root_entity,
        "entities": entities,
        "validation_rules": rules,
    }


def dict_to_spec(data: dict[str, Any]) -> ProfileSpec:
    """Convert a dict back to ProfileSpec."""
    entities = {}
    for name, entity_data in data.get("entities", {}).items():
        fields = []
        for f in entity_data.get("fields", []):
            constraints = None
            if f.get("constraints"):
                c = f["constraints"]
                constraints = Constraints(
                    min_length=c.get("min_length"),
                    max_length=c.get("max_length"),
                    minimum=c.get("minimum"),
                    maximum=c.get("maximum"),
                    pattern=c.get("pattern"),
                    enum=c.get("enum"),
                )
            fields.append(
                FieldSpec(
                    name=f["name"],
                    type=FieldType(f["type"]),
                    required=f.get("required", False),
                    description=f.get("description"),
                    items=f.get("items"),
                    ontology_term=f.get("ontology_term"),
                    constraints=constraints,
                )
            )
        entities[name] = EntityDefSpec(
            ontology_term=entity_data.get("ontology_term"),
            description=entity_data.get("description", ""),
            fields=fields,
        )

    rules = []
    for r in data.get("validation_rules", []):
        rules.append(
            ValidationRuleSpec(
                name=r["name"],
                description=r.get("description", ""),
                expression=r["expression"],
                level=r.get("level", "error"),
            )
        )

    return ProfileSpec(
        name=data.get("name", "unnamed"),
        version=data.get("version", "0.1"),
        display_name=data.get("display_name"),
        description=data.get("description"),
        ontology=data.get("ontology"),
        root_entity=data.get("root_entity"),
        entities=entities,
        validation_rules=rules,
    )


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
