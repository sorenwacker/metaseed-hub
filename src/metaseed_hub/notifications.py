"""Telling a person what happened to their items.

One table, written where the thing happens: the sharing rules record a share, a
changed role and a removal; the comment routes record a comment and a reply.
An entry is added to the session, not committed, so it is stored with the
change it reports or not at all.

Reading is the other half: the bell's count, and the list, which marks what it
shows as read and drops what is older than the retention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select, update

from metaseed_hub.models import ErrorEvent, Notification, User
from metaseed_hub.sharing import Role

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.sharing import SharedResource

RETENTION = timedelta(days=90)
"""How long an entry is kept."""

LIST_LIMIT = 200
"""The most entries the list shows; ninety days of one person's items fit."""


class Kind(StrEnum):
    """What happened."""

    SHARED = "shared"
    ROLE_CHANGED = "role_changed"
    ACCESS_REMOVED = "access_removed"
    COMMENT = "comment"
    REPLY = "reply"
    IMPORT_FINISHED = "import_finished"
    SERVER_ERROR = "server_error"


#: Each kind as the words before the item's title and the words after it.
_PHRASES: dict[Kind, tuple[str, str]] = {
    Kind.SHARED: ("shared the {word}", " with you as {detail}."),
    Kind.ROLE_CHANGED: ("changed your role on the {word}", " to {detail}."),
    Kind.ACCESS_REMOVED: ("removed your access to the {word}", "."),
    Kind.COMMENT: ("commented on the {word}", "."),
    Kind.REPLY: ("replied to your comment on the {word}", "."),
    Kind.IMPORT_FINISHED: ("Finished importing", ": {detail}."),
    Kind.SERVER_ERROR: ("A server error occurred:", "."),
}

#: Kinds with nobody behind them to name: the person's own background work,
#: and an error the platform reports.
_WITHOUT_ACTOR = frozenset({Kind.IMPORT_FINISHED, Kind.SERVER_ERROR})

_WORDS = {
    "dataset": "dataset",
    "draft": "specification draft",
    "spec": "specification",
    "import": "import",
    "error": "error",
}

_URLS = {
    "dataset": "/hub/datasets/{id}",
    "draft": "/hub/spec-builder/{id}",
    "spec": "/hub/spec-builder/spec/{id}",
    "import": "/hub/datasets/new#repository",
    "error": "/hub/admin/#errors",
}


def _title(resource_kind: str, thing: Any) -> str:
    """What the item is called, as the lists call it."""
    if thing is None:
        return ""
    name = str(thing.name)
    return name if resource_kind == "dataset" else f"{name} {thing.version}"


async def record(
    session: AsyncSession,
    *,
    user_id: str,
    actor_id: str,
    kind: Kind,
    resource: SharedResource,
    resource_id: str,
    detail: str | None = None,
) -> None:
    """Add an entry telling ``user_id`` what ``actor_id`` did.

    Nobody is told of their own action. The entry is added to the session and
    left for the caller to commit with the change it reports.

    Args:
        session: Database session.
        user_id: Who is told.
        actor_id: Who did it.
        kind: What happened.
        resource: The kind of item it happened to.
        resource_id: The item.
        detail: The role, for the kinds whose sentence names one.
    """
    if str(user_id) == str(actor_id):
        return
    thing = await session.get(resource.model, resource_id)
    session.add(
        Notification(
            user_id=user_id,
            actor_id=actor_id,
            kind=kind.value,
            resource_kind=resource.kind,
            resource_id=resource_id,
            resource_title=_title(resource.kind, thing),
            detail=detail,
        )
    )


async def import_finished(
    session: AsyncSession,
    *,
    user_id: str,
    job_id: str,
    title: str,
    summary: str,
) -> None:
    """Tell ``user_id`` that their background import is over.

    Their own work, so it has no actor: :func:`record` refuses to tell a person
    of their own action, and a finished job is the one case where that is
    the point. Left for the caller to commit with the job's final state.

    Args:
        session: Database session.
        user_id: Who started the import.
        job_id: The job, which the entry opens the repository tab on.
        title: What was imported, e.g. ``3 ena records``.
        summary: How it went, e.g. ``2 imported, 1 failed``.
    """
    session.add(
        Notification(
            user_id=user_id,
            actor_id=None,
            kind=Kind.IMPORT_FINISHED.value,
            resource_kind="import",
            resource_id=job_id,
            resource_title=title[:255],
            detail=summary[:64],
        )
    )


#: How long one exception type on one path is notified once. An error that
#: repeats on every request would otherwise fill every admin's bell.
ERROR_QUIET_PERIOD = timedelta(hours=1)


async def error_recorded(session: AsyncSession, event: ErrorEvent) -> None:
    """Tell every admin of ``event``, unless its like was told within the hour.

    "Its like" is the same exception type on the same path; the message is
    left out, since it often carries an id that differs on every request.
    Added to the session for the caller to commit with the event.

    Args:
        session: Database session, with ``event`` flushed into it.
        event: The error just recorded.
    """
    from metaseed_hub.admins import admin_ids

    earlier = await session.scalar(
        select(ErrorEvent.id)
        .where(
            ErrorEvent.id != event.id,
            ErrorEvent.exception_type == event.exception_type,
            ErrorEvent.path == event.path,
            ErrorEvent.occurred_at >= datetime.now(UTC) - ERROR_QUIET_PERIOD,
        )
        .limit(1)
    )
    if earlier is not None:
        return
    title = f"{event.exception_type} on {event.method} {event.path}"[:255]
    for admin_id in await admin_ids(session):
        session.add(
            Notification(
                user_id=admin_id,
                actor_id=None,
                kind=Kind.SERVER_ERROR.value,
                resource_kind="error",
                resource_id=event.id,
                resource_title=title,
            )
        )


async def comment_posted(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    actor_id: str,
    parent_author_id: str | None,
    publisher_id: str | None = None,
) -> None:
    """Tell the owners of an item of a comment, and the author replied to of the reply.

    One entry per person: an owner who wrote the comment replied to gets the
    reply, which says more. A published specification often has no member
    rows at all, so whoever published it is named by the caller and told as
    an owner.
    """
    told: set[str] = set()
    if parent_author_id is not None:
        await record(
            session,
            user_id=parent_author_id,
            actor_id=actor_id,
            kind=Kind.REPLY,
            resource=resource,
            resource_id=resource_id,
        )
        told.add(str(parent_author_id))
    owners = await session.execute(
        select(resource.member_model.user_id).where(
            resource.owns_column() == resource_id, resource.member_model.role == Role.OWNER
        )
    )
    for owner_id in [*owners.scalars().all(), *([publisher_id] if publisher_id else [])]:
        if str(owner_id) not in told:
            told.add(str(owner_id))
            await record(
                session,
                user_id=owner_id,
                actor_id=actor_id,
                kind=Kind.COMMENT,
                resource=resource,
                resource_id=resource_id,
            )


async def unread_count(session: AsyncSession, user_id: str) -> int:
    """How many entries ``user_id`` has not opened the list on."""
    count = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            Notification.created_at >= datetime.now(UTC) - RETENTION,
        )
    )
    return int(count or 0)


@dataclass(frozen=True)
class Entry:
    """One notification as the list shows it."""

    actor: str
    lead: str
    """The words before the item's title, the actor included."""
    title: str
    tail: str
    url: str | None
    """Where the item opens, or None when it cannot be opened any more."""
    created_at: datetime
    was_unread: bool

    @property
    def sentence(self) -> str:
        return f"{self.lead} {self.title}{self.tail}"


def _entry(notification: Notification, actor_name: str | None) -> Entry:
    kind = Kind(notification.kind)
    before, after = _PHRASES[kind]
    actor = "" if kind in _WITHOUT_ACTOR else (actor_name or "Someone")
    lead = before.format(word=_WORDS[notification.resource_kind])
    if actor:
        lead = f"{actor} {lead}"
    return Entry(
        actor=actor,
        lead=lead,
        title=notification.resource_title,
        tail=after.format(detail=notification.detail),
        url=None
        if kind is Kind.ACCESS_REMOVED
        else _URLS[notification.resource_kind].format(id=notification.resource_id),
        created_at=notification.created_at,
        was_unread=notification.read_at is None,
    )


async def open_list(session: AsyncSession, user_id: str) -> list[Entry]:
    """The person's entries, newest first; opening them marks them read.

    Entries past the retention are deleted here, so the table holds no more
    than the list could ever show.
    """
    await session.execute(
        delete(Notification).where(
            Notification.user_id == user_id,
            Notification.created_at < datetime.now(UTC) - RETENTION,
        )
    )
    rows = await session.execute(
        select(Notification, User.display_name)
        .outerjoin(User, User.id == Notification.actor_id)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(LIST_LIMIT)
    )
    entries = [_entry(notification, actor_name) for notification, actor_name in rows.all()]
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return entries
