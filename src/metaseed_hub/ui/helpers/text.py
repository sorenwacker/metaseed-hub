"""Field-name text transformations for display."""

import re


def humanize_field_name(name: str) -> str:
    """Convert camelCase or snake_case field name to human-readable format.

    Examples:
        occurrenceID -> Occurrence ID
        basisOfRecord -> Basis Of Record
        unique_id -> Unique Id
    """
    if not name:
        return name
    # First replace underscores with spaces
    name = name.replace("_", " ")
    # Insert space before uppercase letters (for camelCase)
    result = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Handle consecutive uppercase (like ID, URL)
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", result)
    # Title case and return
    return result.title()


def escape_pattern_hyphen(pattern: str) -> str:
    """Escape hyphens in regex character classes for HTML pattern attribute.

    Modern browsers use RegExp 'v' flag which requires escaping hyphens
    that are not part of a valid range (like a-z or 0-9).
    Common problematic pattern: [A-Za-z0-9_-] where _- is not a valid range.
    """
    if not pattern:
        return pattern
    # Escape hyphens that follow underscore or other non-range chars
    # Pattern: _-] or _-x where x is not forming a valid range
    result = re.sub(r"(_)-(\])", r"\1\\-\2", pattern)
    result = re.sub(r"(_)-([^\]])", r"\1\\-\2", result)
    return result
