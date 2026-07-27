"""Recording unhandled server errors so admins can see them in the hub.

The app runs several uvicorn workers, each logging to its own stream, and the
admin dashboard has no privilege to read the host's journal. Recording each
unhandled exception as a row makes the same information visible in the UI,
across workers, and after a restart.

Recording is deliberately passive: the exception is re-raised untouched, so the
usual 500 response and the log line are unchanged. A failure to record is
swallowed, since losing the record of an error is better than replacing it with
a different one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from metaseed_hub.models import ErrorEvent, User

logger = logging.getLogger("metaseed_hub")

# Long enough to investigate a report that arrives days later, short enough that
# the table does not grow without bound.
RETENTION = timedelta(days=30)

# Exception messages can be long; keep them readable and bounded.
_MAX_MESSAGE = 2000


async def record_error(session: AsyncSession, request: Request, exc: BaseException) -> None:
    """Store one unhandled error. Never raises."""
    try:
        user_id = await _caller_id(session, request)
        session.add(
            ErrorEvent(
                method=request.method,
                path=str(request.url.path)[:500],
                exception_type=type(exc).__name__[:200],
                message=(str(exc) or repr(exc))[:_MAX_MESSAGE],
                user_id=user_id,
            )
        )
        await session.execute(
            delete(ErrorEvent).where(ErrorEvent.occurred_at < datetime.now(UTC) - RETENTION)
        )
        await session.commit()
    except Exception:
        logger.exception("Could not record an error event")


async def _caller_id(session: AsyncSession, request: Request) -> str | None:
    """The database id of the signed-in caller, or None if not identifiable."""
    try:
        from metaseed_hub.ui.dependencies import get_current_user_from_cookie

        token_user = await get_current_user_from_cookie(request)
        if token_user is None:
            return None
        result = await session.execute(
            select(User.id).where(User.keycloak_id == token_user.keycloak_id)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None


class ErrorRecordingMiddleware(BaseHTTPMiddleware):
    """Record unhandled exceptions, then let them propagate unchanged."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Pass the request through, recording anything that escapes it."""
        try:
            return await call_next(request)  # type: ignore[no-any-return]
        except Exception as exc:
            from metaseed_hub.database import db

            try:
                async with db.session_factory() as session:
                    await record_error(session, request, exc)
            except Exception:
                logger.exception("Could not open a session to record an error event")
            raise
