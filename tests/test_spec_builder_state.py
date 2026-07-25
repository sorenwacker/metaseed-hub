"""Tests that SpecBuilderState serialization is lossless.

A draft is cloned from a published spec via SpecBuilderState.from_dict and saved
back via to_dict. The serialization must therefore preserve every field of the
ProfileSpec schema, including spec_version, ontologies, entity examples, and the
coordinate/date-range validation-rule fields.
"""

from metaseed.specs.schema import (
    EntityDefSpec,
    FieldSpec,
    FieldType,
    OntologyDefinition,
    ProfileSpec,
    ValidationRuleSpec,
)

from metaseed_hub.ui.spec_builder.state import SpecBuilderState


def _rich_spec() -> ProfileSpec:
    return ProfileSpec(
        name="P",
        version="1.0",
        spec_version="2.0",
        ontologies={"envo": OntologyDefinition(name="ENVO", uri="http://envo")},
        root_entity="Investigation",
        entities={
            "Investigation": EntityDefSpec(
                description="An investigation",
                fields=[
                    FieldSpec(
                        name="studies",
                        type=FieldType.LIST,
                        items="Study",
                        owns=True,
                        tier="required",
                        label="Studies",
                        unit="count",
                        example="STU-1",
                        options=["a", "b"],
                    ),
                    FieldSpec(
                        name="unique_id",
                        type=FieldType.STRING,
                        is_identifier=True,
                        is_label=True,
                    ),
                ],
                example={"unique_id": "INV-1"},
            )
        },
        validation_rules=[
            ValidationRuleSpec(
                name="coords",
                description="lat/lon present",
                applies_to="all",
                type="coordinate_pair",
                message="bad coordinates",
                lat_field="latitude",
                lon_field="longitude",
            ),
            ValidationRuleSpec(
                name="dates",
                description="range valid",
                applies_to="all",
                type="date_range",
                start_field="start_date",
                end_field="end_date",
            ),
        ],
    )


def test_state_roundtrip_preserves_all_spec_fields() -> None:
    """to_dict/from_dict round-trips a rich spec without dropping fields."""
    state = SpecBuilderState(spec=_rich_spec())

    restored = SpecBuilderState.from_dict(state.to_dict())

    assert restored.spec is not None
    spec = restored.spec
    # Profile-level fields the old manual serializer dropped.
    assert spec.spec_version == "2.0"
    assert spec.ontologies is not None
    assert spec.ontologies["envo"].name == "ENVO"
    # Entity-level field the old serializer dropped.
    assert spec.entities["Investigation"].example == {"unique_id": "INV-1"}
    # spec_version 0.6 field markers the builder must preserve (the ones the hub
    # editor now exposes: owns / is_identifier / is_label / tier / label / unit /
    # example / options).
    fields = {f.name: f for f in spec.entities["Investigation"].fields}
    studies = fields["studies"]
    assert studies.owns is True
    assert studies.tier == "required"
    assert studies.label == "Studies"
    assert studies.unit == "count"
    assert studies.example == "STU-1"
    assert studies.options == ["a", "b"]
    uid = fields["unique_id"]
    assert uid.is_identifier is True
    assert uid.is_label is True
    # Validation-rule fields the old serializer dropped.
    coords = next(r for r in spec.validation_rules if r.name == "coords")
    assert coords.type == "coordinate_pair"
    assert coords.message == "bad coordinates"
    assert coords.lat_field == "latitude"
    assert coords.lon_field == "longitude"
    dates = next(r for r in spec.validation_rules if r.name == "dates")
    assert dates.type == "date_range"
    assert dates.start_field == "start_date"
    assert dates.end_field == "end_date"


def test_state_roundtrip_preserves_basic_spec() -> None:
    """A minimal spec still round-trips correctly."""
    state = SpecBuilderState(spec=ProfileSpec(name="Basic", version="0.1"))

    restored = SpecBuilderState.from_dict(state.to_dict())

    assert restored.spec is not None
    assert restored.spec.name == "Basic"
    assert restored.spec.version == "0.1"
