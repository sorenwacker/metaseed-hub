"""Form processing utilities for Hub UI routes."""

from typing import Any

from starlette.datastructures import FormData


def parse_form_field(value: str, field_type: str) -> Any:
    """Convert form string value to appropriate Python type.

    Args:
        value: Raw string value from form.
        field_type: Type name from schema (string, integer, float, boolean).

    Returns:
        Converted value of appropriate type.

    Raises:
        ValueError: If conversion fails.
    """
    if not value:
        return None

    if field_type == "integer":
        return int(value)
    elif field_type == "float":
        return float(value)
    elif field_type == "boolean":
        return value.lower() in ("true", "1", "yes", "on")
    else:
        # string, date, datetime, uri, etc. stay as strings
        return value


def extract_entity_values(
    form_data: FormData,
    helper: Any,
    existing_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract and type-convert form values for an entity.

    Args:
        form_data: FastAPI form data from request.
        helper: EntityHelper from facade with field info.
        existing_values: Optional dict of existing values for update.

    Returns:
        Dictionary of field names to typed values.
    """
    values: dict[str, Any] = {}

    # Start with existing values if provided (for updates)
    if existing_values:
        for field_name in helper.all_fields:
            info = helper.field_info(field_name)
            # Only copy non-nested existing values
            if info.get("type") not in ("list", "entity") and field_name in existing_values:
                values[field_name] = existing_values[field_name]

    # Override with form values
    for field_name in helper.all_fields:
        raw_value = form_data.get(field_name)
        if raw_value is None:
            continue

        raw_str = str(raw_value)

        if raw_str == "":
            # Empty string clears the field (but keep inherited fields)
            if field_name in values and field_name.endswith("_id"):
                continue
            values[field_name] = None
            continue

        info = helper.field_info(field_name)
        field_type = info.get("type", "string")

        try:
            values[field_name] = parse_form_field(raw_str, field_type)
        except ValueError:
            # Keep original string if conversion fails
            values[field_name] = raw_str

    # Remove None values
    return {k: v for k, v in values.items() if v is not None}


def get_label_from_values(values: dict[str, Any]) -> str | None:
    """Extract label from entity values using common identifier fields.

    Args:
        values: Dictionary of entity field values.

    Returns:
        Label string or None if no suitable field found.
    """
    # Check common identifier fields
    for label_field in ("title", "name", "unique_id", "alias", "id", "identifier"):
        if values.get(label_field):
            return str(values[label_field])

    # Special case for Person: combine first_name and last_name
    if values.get("first_name") or values.get("last_name"):
        parts = [values.get("first_name", ""), values.get("last_name", "")]
        return " ".join(p for p in parts if p)

    return None
