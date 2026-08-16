"""Field-name text transformations for display."""

import re


def humanize_field_name(name: str) -> str:
    """Convert camelCase or snake_case field name to human-readable format.

    Examples:
        occurrenceID -> Occurrence Id
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


def safe_filename(name: str, *, fallback: str = "download") -> str:
    """A user-supplied name reduced to something safe in a header.

    Interpolated into ``Content-Disposition: attachment; filename="{}"``. A
    quote closes the filename and lets the rest read as further header
    content; a newline splits the header outright. Both are the user's own
    text — a dataset or spec name — so they are removed here rather than
    forbidden at the point of naming.

    Args:
        name: The user-supplied name.
        fallback: What to use when nothing survives.

    Returns:
        A filename-safe string, never empty.
    """
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_.")
    return cleaned or fallback
