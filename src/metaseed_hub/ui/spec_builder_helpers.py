"""Helper functions for the Spec Builder in Metaseed Hub.

Provides utilities for creating, cloning, converting, and saving ProfileSpec
objects used by the spec builder UI.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

import yaml


def slugify_spec_name(name: str) -> str:
    """Convert a spec name to lowercase slug format.

    Converts CamelCase, spaces, and underscores to hyphens.
    Removes invalid characters.

    Args:
        name: The input name (e.g., "MySpec", "My Spec", "my_spec")

    Returns:
        Lowercase slug (e.g., "my-spec")
    """
    if not name:
        return ""
    # Insert hyphen before uppercase letters (for CamelCase)
    s = re.sub(r"([a-z])([A-Z])", r"\1-\2", name)
    # Replace spaces and underscores with hyphens
    s = re.sub(r"[\s_]+", "-", s)
    # Remove invalid characters (keep only alphanumeric and hyphens)
    s = re.sub(r"[^a-zA-Z0-9-]", "", s)
    # Collapse multiple hyphens
    s = re.sub(r"-+", "-", s)
    # Strip leading/trailing hyphens and lowercase
    return s.strip("-").lower()


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


class _SpecDumper(yaml.Dumper):
    """Dumper for spec YAML output with the custom string representer.

    A dedicated subclass keeps the representer registration local: registering
    it on the default ``yaml.Dumper`` would silently change how every other
    ``yaml.dump`` call in the process renders multi-line strings.
    """


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    """Render multi-line strings in block style for readable spec output."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_SpecDumper.add_representer(str, _str_representer)


def spec_to_yaml(spec: ProfileSpec) -> str:
    """Convert a ProfileSpec to YAML string.

    Args:
        spec: The ProfileSpec to convert.

    Returns:
        YAML string representation of the spec.
    """
    # Convert to dict, using mode='json' to serialize enums as strings
    data = spec.model_dump(mode="json", exclude_none=True, exclude_defaults=False)

    result: str = yaml.dump(
        data,
        Dumper=_SpecDumper,
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
        Dictionary representation of the spec with enums as strings.
    """
    result: dict[str, Any] = spec.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
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


def spec_label(record: Any) -> str:
    """Return the name to show for a specification or draft.

    Profiles carry both a slug (``jerm``) and a display name (``JERM``), and the
    slug alone reads as a typo. The display name is only trustworthy when it is
    the same name differently cased: it is copied from the template a draft was
    cloned from and is not rewritten when the author renames their spec, so a
    spec called "Test" can still carry "ENA".

    Args:
        record: A ``Spec`` or ``SpecDraft`` (anything with ``name`` and
            ``spec_data``).

    Returns:
        The display name when it matches the stored name apart from case,
        otherwise the stored name.
    """
    name = (getattr(record, "name", "") or "").strip()
    data = getattr(record, "spec_data", None) or {}
    display = ((data.get("spec") or {}).get("display_name") or "").strip()
    if display and display.lower() == name.lower():
        return display
    return name or "Untitled"
