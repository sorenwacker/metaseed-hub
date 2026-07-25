"""Bounded reading of uploaded files."""

from fastapi import HTTPException, UploadFile

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
