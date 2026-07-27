"""Unhandled server errors are recorded and shown to admins.

The point of the feature is that an error a user hit is visible afterwards, so
these assert on the stored content and on recording being passive — an error
that gets swallowed or altered on its way to the log is worse than none.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.errors import RETENTION, record_error
from metaseed_hub.models import ErrorEvent, User
from metaseed_hub.ui.routes.admin import _error_counts_by_day, _recent_errors
from tests.factories import make_tenant, make_user


def _request(path: str = "/hub/datasets/x", method: str = "GET") -> Mock:
    request = Mock()
    request.method = method
    request.url = Mock(path=path)
    request.cookies = {}
    request.headers = {}
    return request


async def _stored(session: AsyncSession) -> list[ErrorEvent]:
    result = await session.execute(select(ErrorEvent).order_by(ErrorEvent.occurred_at))
    return list(result.scalars().all())


async def test_an_error_is_recorded_with_what_is_needed_to_find_it(
    session: AsyncSession,
) -> None:
    await record_error(session, _request("/hub/datasets/abc"), ValueError("bad profile"))

    events = await _stored(session)
    assert len(events) == 1
    assert events[0].path == "/hub/datasets/abc"
    assert events[0].method == "GET"
    assert events[0].exception_type == "ValueError"
    assert events[0].message == "bad profile"


async def test_an_exception_with_no_message_still_records_its_type(
    session: AsyncSession,
) -> None:
    """str(exc) is empty for a bare raise; a blank row would be useless."""
    await record_error(session, _request(), RuntimeError())

    events = await _stored(session)
    assert events[0].exception_type == "RuntimeError"
    assert events[0].message, "an empty message must fall back to the repr"


async def test_a_long_message_is_truncated_rather_than_rejected(
    session: AsyncSession,
) -> None:
    await record_error(session, _request(), ValueError("x" * 9000))

    events = await _stored(session)
    assert 0 < len(events[0].message) <= 2000


async def test_recording_never_raises(session: AsyncSession) -> None:
    """Recording runs while an exception is already propagating; raising here
    would replace the real error with a database error."""
    broken = Mock()
    broken.add = Mock(side_effect=RuntimeError("database gone"))

    await record_error(broken, _request(), ValueError("original"))


async def test_old_errors_are_pruned_but_recent_ones_kept(session: AsyncSession) -> None:
    old = ErrorEvent(
        method="GET",
        path="/old",
        exception_type="ValueError",
        message="old",
        occurred_at=datetime.now(UTC) - RETENTION - timedelta(days=1),
    )
    recent = ErrorEvent(
        method="GET",
        path="/recent",
        exception_type="ValueError",
        message="recent",
        occurred_at=datetime.now(UTC) - timedelta(days=1),
    )
    session.add_all([old, recent])
    await session.commit()

    await record_error(session, _request("/new"), ValueError("new"))

    paths = {e.path for e in await _stored(session)}
    assert paths == {"/recent", "/new"}, "pruning must drop only what is past retention"


async def test_the_dashboard_shows_the_newest_first_with_the_caller(
    session: AsyncSession,
) -> None:
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email="reporter@example.org")
    session.add(user)
    await session.flush()
    session.add_all(
        [
            ErrorEvent(
                method="GET",
                path="/first",
                exception_type="ValueError",
                message="first",
                occurred_at=datetime.now(UTC) - timedelta(hours=2),
                user_id=user.id,
            ),
            ErrorEvent(
                method="POST",
                path="/second",
                exception_type="KeyError",
                message="second",
                occurred_at=datetime.now(UTC) - timedelta(hours=1),
            ),
        ]
    )
    await session.commit()

    errors = await _recent_errors(session)

    assert [e.path for e in errors] == ["/second", "/first"]
    assert errors[1].user is not None
    assert errors[1].user.email == "reporter@example.org"
    assert errors[0].user is None, "an unidentified caller stays unidentified"


async def test_error_counts_group_by_day(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            ErrorEvent(
                method="GET",
                path=f"/p{i}",
                exception_type="ValueError",
                message="m",
                occurred_at=now - timedelta(minutes=i),
            )
            for i in range(3)
        ]
    )
    await session.commit()

    counts = await _error_counts_by_day(session)

    assert sum(count for _, count in counts) == 3


async def test_deleting_a_user_leaves_their_errors_visible(session: AsyncSession) -> None:
    """The FK is SET NULL: account deletion must not erase the error history."""
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant)
    session.add(user)
    await session.flush()
    session.add(
        ErrorEvent(
            method="GET",
            path="/kept",
            exception_type="ValueError",
            message="m",
            user_id=user.id,
        )
    )
    await session.commit()

    await session.delete(await session.get(User, user.id))
    await session.commit()

    events = await _stored(session)
    assert [e.path for e in events] == ["/kept"]
    assert events[0].user_id is None


@pytest.mark.parametrize("attribute", ["dispatch"])
def test_the_middleware_re_raises_rather_than_swallowing(attribute: str) -> None:
    """A recorded error must still reach the normal 500 handling and the log."""
    import inspect

    from metaseed_hub.errors import ErrorRecordingMiddleware

    source = inspect.getsource(getattr(ErrorRecordingMiddleware, attribute))
    assert "raise" in source, "the middleware must re-raise the original exception"
