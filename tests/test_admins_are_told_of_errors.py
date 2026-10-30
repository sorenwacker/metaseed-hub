"""Administrators are told of server errors through the notification bell.

An error was only visible to someone who opened the dashboard. Now the admins
get a notification, once per exception type and path per hour, so a failure
repeating on every request does not bury the bell.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from metaseed_hub.collaborations import record_memberships
from metaseed_hub.errors import record_error
from metaseed_hub.models import ErrorEvent, Notification
from metaseed_hub.notifications import open_list
from tests.factories import make_tenant, make_user

pytestmark = pytest.mark.asyncio

ADMINS = "urn:mace:surf.nl:sram:group:tudelft:admin"
OTHERS = "urn:mace:surf.nl:sram:group:tudelft:cropxr"


def _request(path: str) -> Request:
    return Request(
        {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
    )


async def _person(session: AsyncSession, groups: list[str]) -> str:
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=f"person-{uuid4().hex[:6]}@example.org")
    session.add(user)
    await session.flush()
    await record_memberships(session, user.id, groups)
    await session.commit()
    return str(user.id)


async def _told(session: AsyncSession, user_id: str) -> list[Notification]:
    return list(
        (await session.execute(select(Notification).where(Notification.user_id == user_id)))
        .scalars()
        .all()
    )


async def test_an_admin_is_told_and_nobody_else(session: AsyncSession) -> None:
    admin = await _person(session, [ADMINS])
    member = await _person(session, [OTHERS])

    await record_error(session, _request("/hub/datasets/x"), KeyError("entity"))

    entries = await open_list(session, admin)
    assert [(e.title, e.url) for e in entries] == [
        ("KeyError on GET /hub/datasets/x", "/hub/admin/#errors")
    ]
    assert await _told(session, member) == []


async def test_a_repeat_within_the_hour_is_told_once(session: AsyncSession) -> None:
    admin = await _person(session, [ADMINS])

    await record_error(session, _request("/hub/a"), KeyError("one"))
    await record_error(session, _request("/hub/a"), KeyError("two"))
    await record_error(session, _request("/hub/b"), KeyError("three"))
    await record_error(session, _request("/hub/a"), ValueError("four"))

    titles = sorted(n.resource_title for n in await _told(session, admin))
    assert titles == [
        "KeyError on GET /hub/a",
        "KeyError on GET /hub/b",
        "ValueError on GET /hub/a",
    ]
    assert len((await session.execute(select(ErrorEvent))).scalars().all()) == 4


async def test_the_same_error_an_hour_later_is_told_again(session: AsyncSession) -> None:
    admin = await _person(session, [ADMINS])
    session.add(
        ErrorEvent(
            occurred_at=datetime.now(UTC) - timedelta(minutes=61),
            method="GET",
            path="/hub/a",
            exception_type="KeyError",
            message="earlier",
        )
    )
    await session.commit()

    await record_error(session, _request("/hub/a"), KeyError("later"))

    assert len(await _told(session, admin)) == 1
