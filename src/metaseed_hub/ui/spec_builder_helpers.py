"""Helper functions for the Spec Builder in Metaseed Hub.

Provides utilities for creating, cloning, converting, and saving ProfileSpec
objects used by the spec builder UI.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from metaseed.specs.schema import ProfileSpec


def create_empty_spec() -> ProfileSpec:
    """Create a new empty ProfileSpec scaffold.

    Returns:
        A ProfileSpec with default values ready for editing.
    """
    from metaseed.specs.schema import ProfileSpec

    return ProfileSpec(
        version="0.1",
        name="",
        display_name="",
        description="",
        ontology=None,
        root_entity="",
        validation_rules=[],
        entities={},
    )


def clone_spec(profile: str, version: str) -> ProfileSpec:
    """Deep copy an existing spec for use as a template.

    Args:
        profile: Profile name (e.g., "miappe").
        version: Version string (e.g., "1.2").

    Returns:
        A deep copy of the ProfileSpec that can be modified independently.

    Raises:
        ValueError: If the profile/version cannot be loaded.
    """
    from metaseed.specs.loader import SpecLoader

    loader = SpecLoader(profile=profile)
    try:
        spec = loader.load_profile(version=version, profile=profile)
    except Exception as e:
        raise ValueError(f"Cannot load profile {profile} v{version}: {e}") from e

    # Deep copy to ensure independence from cached version
    return copy.deepcopy(spec)


def spec_to_yaml(spec: ProfileSpec) -> str:
    """Convert a ProfileSpec to YAML string.

    Args:
        spec: The ProfileSpec to convert.

    Returns:
        YAML string representation of the spec.
    """
    # Convert to dict, handling Pydantic models
    data = spec.model_dump(exclude_none=True, exclude_defaults=False)

    # Custom representer for cleaner output
    def str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    yaml.add_representer(str, str_representer)

    result: str = yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=120,
    )
    return result


def spec_to_dict(spec: ProfileSpec) -> dict[str, Any]:
    """Convert a ProfileSpec to a dictionary.

    Args:
        spec: The ProfileSpec to convert.

    Returns:
        Dictionary representation of the spec.
    """
    result: dict[str, Any] = spec.model_dump(exclude_none=True, exclude_defaults=False)
    return result


def list_available_templates() -> list[dict[str, Any]]:
    """List available profiles that can be used as templates.

    Returns:
        List of dicts with profile info: name, display_name, versions.
    """
    from metaseed.specs.loader import SpecLoader

    loader = SpecLoader()
    profiles = loader.list_profiles()

    result = []
    for profile_name in profiles:
        versions = loader.list_versions(profile_name)
        if not versions:
            continue

        # Get display info from latest version
        try:
            latest = versions[-1]
            spec = loader.load_profile(version=latest, profile=profile_name)
            result.append(
                {
                    "name": profile_name,
                    "display_name": spec.display_name or profile_name.upper(),
                    "description": spec.description or "",
                    "versions": versions,
                }
            )
        except Exception:
            result.append(
                {
                    "name": profile_name,
                    "display_name": profile_name.upper(),
                    "description": "",
                    "versions": versions,
                }
            )

    return result


def validate_entity_name(name: str) -> str | None:
    """Validate an entity name.

    Args:
        name: The entity name to validate.

    Returns:
        Error message if invalid, None if valid.
    """
    if not name:
        return "Entity name is required"
    if not name[0].isupper():
        return "Entity name must start with uppercase letter (PascalCase)"
    if not name.replace("_", "").isalnum():
        return "Entity name can only contain letters, numbers, and underscores"
    return None


def validate_field_name(name: str) -> str | None:
    """Validate a field name.

    Args:
        name: The field name to validate.

    Returns:
        Error message if invalid, None if valid.
    """
    if not name:
        return "Field name is required"
    if not name[0].islower() and name[0] != "_":
        return "Field name must start with lowercase letter (snake_case)"
    if not name.replace("_", "").isalnum():
        return "Field name can only contain letters, numbers, and underscores"
    return None


def parse_spec_from_yaml(yaml_content: str) -> ProfileSpec:
    """Parse a YAML string into a ProfileSpec.

    Args:
        yaml_content: YAML string containing spec definition.

    Returns:
        ProfileSpec object parsed from the YAML.

    Raises:
        ValueError: If YAML is invalid or cannot be parsed as ProfileSpec.
    """
    from metaseed.specs.schema import ProfileSpec

    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML syntax: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("YAML must contain a mapping/dictionary at the root level")

    # Handle case where spec is nested under 'spec' key (SpecBuilderState format)
    if "spec" in data and isinstance(data["spec"], dict):
        data = data["spec"]

    try:
        return ProfileSpec.model_validate(data)
    except Exception as e:
        raise ValueError(f"Invalid spec structure: {e}") from e
