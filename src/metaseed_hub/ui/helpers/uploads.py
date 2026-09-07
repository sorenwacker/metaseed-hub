"""Bounded reading and parsing of uploaded files."""

from typing import Any

from fastapi import HTTPException, UploadFile

from metaseed_hub.ui.metaseed_ui import workbook_to_payload

# Upper bound on an uploaded import file, enforced in the app rather than relying
# on the reverse proxy alone. A bare ``await file.read()`` loads the whole upload
# into memory (then parses it), so an unbounded read is a memory-pressure DoS on a
# deployment without an edge cap.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB


async def read_upload_capped(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an uploaded file fully, but refuse anything larger than ``max_bytes``.

    Args:
        file: The uploaded file.
        max_bytes: Maximum number of bytes to accept.

    Returns:
        The file content.

    Raises:
        HTTPException: 413 if the upload exceeds ``max_bytes``.
    """
    # Read one byte past the limit to distinguish "exactly at limit" from "over".
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large; the limit is {max_bytes // (1024 * 1024)} MiB.",
        )
    return content


def parse_workbook_sheets(
    content: bytes, *, profile: str, version: str, facade: Any
) -> dict[str, list[dict[str, Any]]]:
    """Parse an exported ``.xlsx`` workbook into ``{entity_type: [row dicts]}``.

    The parsing itself is metaseed's. The hub used to do it here, and the copy
    was behind the library in ways that changed data on every round trip: it
    never removed the quote the export puts in front of a formula-triggering
    cell, and never split a scalar list back out of the single cell the export
    joined it into. Reading a workbook is the file format's business, and the
    format is the library's.

    Args:
        content: The raw workbook bytes.
        profile: Profile the dataset belongs to; the workbook does not carry it.
        version: Profile version, for the same reason.
        facade: The profile facade, used to know each sheet's entity type.

    Returns:
        Mapping of entity type to its row payloads, in workbook order.

    Raises:
        ValueError: If the file is not a readable workbook, or no sheet matches
            an entity type of the profile.
    """
    payload = workbook_to_payload(content, profile=profile, version=version, facade=facade)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for entity in payload["entities"]:
        by_type.setdefault(str(entity["_type"]), []).append(entity)
    return by_type
