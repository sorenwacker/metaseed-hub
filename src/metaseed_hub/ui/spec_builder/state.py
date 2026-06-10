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


def _field_to_dict(f: FieldSpec) -> dict[str, Any]:
    """Convert a FieldSpec to a JSON-serializable dict."""
    field_dict: dict[str, Any] = {
        "name": f.name,
        "type": f.type.value,
        "required": f.required,
    }
    if f.description:
        field_dict["description"] = f.description
    if f.codename:
        field_dict["codename"] = f.codename
    if f.items:
        field_dict["items"] = f.items
    if f.ontology_term:
        field_dict["ontology_term"] = f.ontology_term
    if f.ontologies:
        field_dict["ontologies"] = f.ontologies
    if f.parent_ref:
        field_dict["parent_ref"] = f.parent_ref
    if f.unique_within:
        field_dict["unique_within"] = f.unique_within
    if f.reference:
        field_dict["reference"] = f.reference
    if f.constraints:
        constraints: dict[str, Any] = {}
        if f.constraints.pattern:
            constraints["pattern"] = f.constraints.pattern
        if f.constraints.min_length is not None:
            constraints["min_length"] = f.constraints.min_length
        if f.constraints.max_length is not None:
            constraints["max_length"] = f.constraints.max_length
        if f.constraints.minimum is not None:
            constraints["minimum"] = f.constraints.minimum
        if f.constraints.maximum is not None:
            constraints["maximum"] = f.constraints.maximum
        if f.constraints.min_items is not None:
            constraints["min_items"] = f.constraints.min_items
        if f.constraints.max_items is not None:
            constraints["max_items"] = f.constraints.max_items
        if f.constraints.enum:
            constraints["enum"] = f.constraints.enum
        if constraints:
            field_dict["constraints"] = constraints
    return field_dict


def _rule_to_dict(rule: ValidationRuleSpec) -> dict[str, Any]:
    """Convert a ValidationRuleSpec to a JSON-serializable dict."""
    rule_dict: dict[str, Any] = {
        "name": rule.name,
        "description": rule.description,
        "applies_to": rule.applies_to,
    }
    if rule.field:
        rule_dict["field"] = rule.field
    if rule.condition:
        rule_dict["condition"] = rule.condition
    if rule.pattern:
        rule_dict["pattern"] = rule.pattern
    if rule.minimum is not None:
        rule_dict["minimum"] = rule.minimum
    if rule.maximum is not None:
        rule_dict["maximum"] = rule.maximum
    if rule.enum:
        rule_dict["enum"] = rule.enum
    if rule.reference:
        rule_dict["reference"] = rule.reference
    if rule.unique_within:
        rule_dict["unique_within"] = rule.unique_within
    if rule.min_items is not None:
        rule_dict["min_items"] = rule.min_items
    if rule.max_items is not None:
        rule_dict["max_items"] = rule.max_items
    return rule_dict


def spec_to_dict(spec: ProfileSpec) -> dict[str, Any]:
    """Convert ProfileSpec to a JSON-serializable dict."""
    entities = {}
    for name, entity in spec.entities.items():
        entities[name] = {
            "ontology_term": entity.ontology_term,
            "description": entity.description,
            "fields": [_field_to_dict(f) for f in entity.fields],
        }

    return {
        "name": spec.name,
        "version": spec.version,
        "display_name": spec.display_name,
        "description": spec.description,
        "ontology": spec.ontology,
        "root_entity": spec.root_entity,
        "entities": entities,
        "validation_rules": [_rule_to_dict(r) for r in spec.validation_rules],
    }


def _dict_to_field(f: dict[str, Any]) -> FieldSpec:
    """Convert a dict back to FieldSpec."""
    constraints = None
    if f.get("constraints"):
        c = f["constraints"]
        constraints = Constraints(
            pattern=c.get("pattern"),
            min_length=c.get("min_length"),
            max_length=c.get("max_length"),
            minimum=c.get("minimum"),
            maximum=c.get("maximum"),
            min_items=c.get("min_items"),
            max_items=c.get("max_items"),
            enum=c.get("enum"),
        )
    return FieldSpec(
        name=f["name"],
        codename=f.get("codename"),
        type=FieldType(f["type"]),
        required=f.get("required", False),
        description=f.get("description") or "",
        ontology_term=f.get("ontology_term"),
        ontologies=f.get("ontologies"),
        constraints=constraints,
        items=f.get("items"),
        parent_ref=f.get("parent_ref"),
        unique_within=f.get("unique_within"),
        reference=f.get("reference"),
    )


def _dict_to_rule(r: dict[str, Any]) -> ValidationRuleSpec:
    """Convert a dict back to ValidationRuleSpec."""
    return ValidationRuleSpec(
        name=r["name"],
        description=r.get("description", ""),
        applies_to=r.get("applies_to", "all"),
        field=r.get("field"),
        condition=r.get("condition"),
        pattern=r.get("pattern"),
        minimum=r.get("minimum"),
        maximum=r.get("maximum"),
        enum=r.get("enum"),
        reference=r.get("reference"),
        unique_within=r.get("unique_within"),
        min_items=r.get("min_items"),
        max_items=r.get("max_items"),
    )


def dict_to_spec(data: dict[str, Any]) -> ProfileSpec:
    """Convert a dict back to ProfileSpec."""
    entities = {}
    for name, entity_data in data.get("entities", {}).items():
        entities[name] = EntityDefSpec(
            ontology_term=entity_data.get("ontology_term"),
            description=entity_data.get("description", ""),
            fields=[_dict_to_field(f) for f in entity_data.get("fields", [])],
        )

    return ProfileSpec(
        name=data.get("name", "unnamed"),
        version=data.get("version", "0.1"),
        display_name=data.get("display_name"),
        description=data.get("description") or "",
        ontology=data.get("ontology"),
        root_entity=data.get("root_entity") or "",
        entities=entities,
        validation_rules=[_dict_to_rule(r) for r in data.get("validation_rules", [])],
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
