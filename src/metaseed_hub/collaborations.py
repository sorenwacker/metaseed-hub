"""SRAM collaborations as the hub knows them: a snapshot per user, read back.

The identity provider states a person's groups at sign-in and nowhere else, so
the hub records that statement (:class:`~metaseed_hub.models.GroupMembership`)
and replaces it at the next sign-in. Everything a collaboration is used for
reads the record: listing who is in one, and matching a collaboration grant
against a caller who arrived with an access token, which carries no
entitlements at all.

A record older than ``membership_max_age_days`` is not trusted: it grants
nothing and lists nobody. That bounds how long someone who left a
collaboration keeps reaching its items without signing in again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from metaseed_hub.config import get_settings
from metaseed_hub.entitlements import (
    SRAM_GROUP_PREFIX,
    collaboration_urn,
    entitled_urns,
    group_urns,
    parse_group,
)
from metaseed_hub.models import GroupMembership, User


class MembershipState(StrEnum):
    """What the hub knows about a person's collaborations, and how it knows it.

    An empty reading and no reading at all look the same in the rows, and mean
    opposite things to the person reading the page: one says their identity
    provider put them in nothing, the other says nobody has asked yet.
    """

    NEVER_READ = "never_read"
    """No reading has been taken. Signing in again takes one."""
    NONE_REPORTED = "none_reported"
    """A reading was taken and the identity provider named no group."""
    CURRENT = "current"
    """A reading within the trusted window named at least one group."""
    STALE = "stale"
    """The reading is older than the trusted window, so it grants nothing."""


if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession


class NotInCollaborationError(Exception):
    """Only members see a collaboration's people or hand it their items."""

    def __init__(self, urn: str) -> None:
        super().__init__(f"You are not in {short_name(urn)}, as of your last sign-in.")


@dataclass(frozen=True)
class Collaboration:
    """One collaboration a user is in, with the groups they are in within it.

    Attributes:
        urn: The collaboration URN, which a grant is written against.
        organisation: The SRAM organisation short name.
        name: The collaboration short name.
        groups: The user's groups within it, sorted.
        read_at: When the hub last read this from the identity provider.
    """

    urn: str
    organisation: str
    name: str
    groups: list[str]
    read_at: datetime


def short_name(urn: str) -> str:
    """``organisation:collaboration[:group]``, the URN without its fixed prefix."""
    return urn[len(SRAM_GROUP_PREFIX) :] if urn.startswith(SRAM_GROUP_PREFIX) else urn


def _fresh_after() -> datetime:
    return datetime.now(UTC) - timedelta(days=get_settings().membership_max_age_days)


async def record_memberships(
    session: AsyncSession,
    user_id: str,
    entitlements: Iterable[str] | None,
    *,
    seen_at: datetime | None = None,
) -> None:
    """Replace what is recorded for ``user_id`` with the groups just reported.

    Authoritative: a reading that names nothing clears what was there, which
    is how leaving every collaboration takes effect. The time is stamped on
    the user, so a reading that found nothing is still a reading.

    Flushed, not committed: the caller commits with the rest of the sign-in.
    ``seen_at`` defaults to now; tests pass a date to age a reading.
    """
    urns = list(dict.fromkeys(group_urns(entitlements)))
    await session.execute(delete(GroupMembership).where(GroupMembership.user_id == user_id))
    session.add_all(GroupMembership(user_id=user_id, urn=urn) for urn in urns)
    user = await session.get(User, user_id)
    if user is not None:
        user.memberships_read_at = seen_at or datetime.now(UTC)
    await session.flush()


async def refresh_memberships(
    session: AsyncSession, user_id: str, entitlements: Iterable[str] | None
) -> None:
    """Take a reading mid-session when there is none, or it has gone stale.

    The sign-in callback was the only place a reading was taken, and a token
    refresh does not re-run it; the refresh cookie lasts thirty days, so a
    session that predated this feature, or simply kept going, never had one
    taken at all.

    Does nothing when ``entitlements`` is empty. A personal access token
    carries none by construction, so treating that as "in nothing" would drop
    a person's access on their next API call. Only a sign-in may clear a
    reading.
    """
    if not list(entitlements or []):
        return
    user = await session.get(User, user_id)
    if user is not None and user.memberships_read_at is not None:
        if user.memberships_read_at >= _fresh_after():
            return
    await record_memberships(session, user_id, entitlements)


async def membership_state(session: AsyncSession, user_id: str) -> MembershipState:
    """What the hub knows about ``user_id``'s collaborations, and how."""
    user = await session.get(User, user_id)
    read_at = user.memberships_read_at if user is not None else None
    if read_at is None:
        return MembershipState.NEVER_READ
    if read_at < _fresh_after():
        return MembershipState.STALE
    return (
        MembershipState.CURRENT
        if await _fresh_rows(session, user_id)
        else MembershipState.NONE_REPORTED
    )


async def _fresh_rows(session: AsyncSession, user_id: str) -> list[GroupMembership]:
    """The recorded groups, or none when the reading is too old to trust."""
    user = await session.get(User, user_id)
    read_at = user.memberships_read_at if user is not None else None
    if read_at is None or read_at < _fresh_after():
        return []
    result = await session.execute(
        select(GroupMembership).where(GroupMembership.user_id == user_id)
    )
    return list(result.scalars().all())


async def entitled_urns_of(session: AsyncSession, user_id: str) -> set[str]:
    """Every URN a grant may be matched against for this user, from the snapshot.

    :func:`metaseed_hub.entitlements.entitled_urns` decides what a set of
    reported groups entitles someone to; this reads the recorded groups and
    asks it. Deriving the collaboration URN here as well gave two
    implementations of one rule, free to drift while both looked right.
    """
    return entitled_urns(row.urn for row in await _fresh_rows(session, user_id))


def grant_label(urn: str) -> str:
    """How a grant is named on a card: the collaboration, and the group if any.

    The organisation is dropped. It is the same for everyone a person shares
    with in practice, so it adds a word without telling them anything.
    """
    group = parse_group(urn)
    if group is not None:
        return f"{group.collaboration} / {group.group}"
    without_prefix = short_name(urn)
    return without_prefix.split(":", 1)[-1] if ":" in without_prefix else without_prefix


async def collaborations_of(session: AsyncSession, user_id: str) -> list[Collaboration]:
    """The collaborations in the user's current reading, sorted by URN."""
    user = await session.get(User, user_id)
    read_at = user.memberships_read_at if user is not None else None
    by_urn: dict[str, Collaboration] = {}
    for row in await _fresh_rows(session, user_id):
        group = parse_group(row.urn)
        if group is None:
            continue
        urn = collaboration_urn(group)
        found = by_urn.get(urn)
        groups = sorted([*found.groups, group.group]) if found else [group.group]
        by_urn[urn] = Collaboration(
            urn=urn,
            organisation=group.organisation,
            name=group.collaboration,
            groups=groups,
            read_at=read_at or datetime.now(UTC),
        )
    return [by_urn[urn] for urn in sorted(by_urn)]


async def people_in(session: AsyncSession, urn: str, *, viewer_id: str) -> list[User]:
    """The live users whose fresh snapshot puts them in collaboration ``urn``.

    Sorted by name, then address. Someone who has never signed in has no
    snapshot and is not listed; SRAM itself remains the authoritative list.

    Raises:
        NotInCollaborationError: If the viewer's own snapshot does not put
            them in it. The people are names and addresses, so only members
            see them.
    """
    if urn not in await entitled_urns_of(session, viewer_id):
        raise NotInCollaborationError(urn)
    result = await session.execute(
        select(User, GroupMembership.urn)
        .join(GroupMembership, GroupMembership.user_id == User.id)
        .where(
            GroupMembership.urn.like(f"{urn}:%"),
            # Someone whose reading has gone stale is not listed, for the same
            # reason it grants them nothing.
            User.memberships_read_at >= _fresh_after(),
            User.deleted_at.is_(None),
        )
        .order_by(User.display_name, User.email)
    )
    people: dict[str, User] = {}
    for user, member_urn in result.all():
        group = parse_group(member_urn)
        if group is not None and collaboration_urn(group) == urn:
            people.setdefault(user.id, user)
    return list(people.values())
