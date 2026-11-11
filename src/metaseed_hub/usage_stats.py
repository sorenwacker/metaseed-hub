"""Aggregate usage figures for the admin dashboard.

Every figure is a count; none names a dataset or reads its content. What each
one counts, and what it cannot, is described in ``docs/administration.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.collaborations import grant_label
from metaseed_hub.models import (
    Dataset,
    DatasetCollaborationGrant,
    DatasetMember,
    DatasetVersion,
    Spec,
    SpecCollaborationGrant,
    SpecDraft,
    SpecDraftCollaborationGrant,
    SpecDraftMember,
    SpecMember,
    SpecStatus,
    User,
)
from metaseed_hub.sharing import Role

#: The period the activity figures cover.
ACTIVE_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class SpecCounts:
    """Specifications by where they come from and who can see them."""

    builtin: int
    published_to_everyone: int
    published_to_a_collaboration: int
    drafts: int
    drafts_shared_with_individuals: int
    drafts_shared_with_a_collaboration: int


@dataclass(frozen=True)
class DatasetCounts:
    """Datasets by sharing state and by the kind of profile they use."""

    total: int
    private: int
    shared_with_individuals: int
    shared_with_a_collaboration: int
    on_builtin_profile: int
    on_published_spec: int
    on_draft: int


@dataclass(frozen=True)
class ShareCounts:
    """Shares with people and with collaborations.

    Attributes:
        individual: Editor and viewer memberships; owners are not shares.
        collaboration: Collaboration and group grants.
        by_collaboration: ``(label, grants)`` per collaboration or group,
            most grants first.
    """

    individual: int
    collaboration: int
    by_collaboration: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ActiveUsers:
    """The two activity figures the hub can measure; see the module docs."""

    signed_in: int
    saved_a_dataset: int


_MEMBERS = (DatasetMember, SpecMember, SpecDraftMember)
_GRANTS = (DatasetCollaborationGrant, SpecCollaborationGrant, SpecDraftCollaborationGrant)


async def _count(session: AsyncSession, query: Select[Any]) -> int:
    return int(await session.scalar(query) or 0)


def _live_datasets() -> ColumnElement[bool]:
    return Dataset.deleted_at.is_(None)


def _published_specs() -> ColumnElement[bool]:
    return (Spec.deleted_at.is_(None)) & (Spec.status == SpecStatus.PUBLISHED)


async def spec_counts(session: AsyncSession, *, builtin: int) -> SpecCounts:
    """Count specifications by source, audience and sharing.

    Args:
        session: Database session.
        builtin: The number of profile versions the metaseed library ships,
            which the hub does not store.

    Returns:
        The counts.
    """
    published = select(func.count(Spec.id)).where(_published_specs())
    shared_draft = exists().where(
        SpecDraftMember.spec_draft_id == SpecDraft.id, SpecDraftMember.role != Role.OWNER
    )
    granted_draft = exists().where(SpecDraftCollaborationGrant.spec_draft_id == SpecDraft.id)
    drafts = select(func.count(SpecDraft.id))
    return SpecCounts(
        builtin=builtin,
        published_to_everyone=await _count(session, published.where(Spec.audience_urn.is_(None))),
        published_to_a_collaboration=await _count(
            session, published.where(Spec.audience_urn.is_not(None))
        ),
        drafts=await _count(session, drafts),
        drafts_shared_with_individuals=await _count(session, drafts.where(shared_draft)),
        drafts_shared_with_a_collaboration=await _count(session, drafts.where(granted_draft)),
    )


async def dataset_counts(session: AsyncSession) -> DatasetCounts:
    """Count live datasets by sharing state and profile source.

    Args:
        session: Database session.

    Returns:
        The counts.
    """
    shared = exists().where(
        DatasetMember.dataset_id == Dataset.id, DatasetMember.role != Role.OWNER
    )
    granted = exists().where(DatasetCollaborationGrant.dataset_id == Dataset.id)
    live = select(func.count(Dataset.id)).where(_live_datasets())
    return DatasetCounts(
        total=await _count(session, live),
        private=await _count(session, live.where(~shared, ~granted)),
        shared_with_individuals=await _count(session, live.where(shared)),
        shared_with_a_collaboration=await _count(session, live.where(granted)),
        on_builtin_profile=await _count(
            session, live.where(Dataset.spec_id.is_(None), Dataset.spec_draft_id.is_(None))
        ),
        on_published_spec=await _count(session, live.where(Dataset.spec_id.is_not(None))),
        on_draft=await _count(
            session, live.where(Dataset.spec_id.is_(None), Dataset.spec_draft_id.is_not(None))
        ),
    )


def _live_resource(table: Any) -> ColumnElement[bool]:
    """The condition keeping shares on deleted datasets and specs out."""
    if table in (DatasetMember, DatasetCollaborationGrant):
        return exists().where(Dataset.id == table.dataset_id, _live_datasets())
    if table in (SpecMember, SpecCollaborationGrant):
        return exists().where(Spec.id == table.spec_id, _published_specs())
    return exists().where(SpecDraft.id == table.spec_draft_id)


async def share_counts(session: AsyncSession) -> ShareCounts:
    """Count individual and collaboration shares.

    Args:
        session: Database session.

    Returns:
        The totals and the grants per collaboration.
    """
    individual = 0
    for member in _MEMBERS:
        individual += await _count(
            session,
            select(func.count())
            .select_from(member)
            .where(member.role != Role.OWNER, _live_resource(member)),
        )
    per_urn: dict[str, int] = {}
    for grant in _GRANTS:
        rows = await session.execute(
            select(grant.urn, func.count()).where(_live_resource(grant)).group_by(grant.urn)
        )
        for urn, count in rows.all():
            per_urn[urn] = per_urn.get(urn, 0) + count
    per_label: dict[str, int] = {}
    for urn, count in per_urn.items():
        label = grant_label(urn)
        per_label[label] = per_label.get(label, 0) + count
    return ShareCounts(
        individual=individual,
        collaboration=sum(per_label.values()),
        by_collaboration=tuple(sorted(per_label.items(), key=lambda item: (-item[1], item[0]))),
    )


async def shares_received_by_user(session: AsyncSession) -> dict[str, int]:
    """Return ``{user_id: individual shares held}`` for users holding any.

    Args:
        session: Database session.

    Returns:
        The counts; a user holding none is absent.
    """
    received: dict[str, int] = {}
    for member in _MEMBERS:
        rows = await session.execute(
            select(member.user_id, func.count())
            .where(member.role != Role.OWNER, _live_resource(member))
            .group_by(member.user_id)
        )
        for user_id, count in rows.all():
            received[user_id] = received.get(user_id, 0) + count
    return received


def _months(first: datetime, last: datetime) -> list[str]:
    year, month = first.year, first.month
    months = []
    while (year, month) <= (last.year, last.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


async def users_by_month(session: AsyncSession, *, now: datetime) -> list[tuple[str, int]]:
    """Return the number of current users registered by the end of each month.

    Args:
        session: Database session.
        now: The current time; the series ends in its month.

    Returns:
        ``(YYYY-MM, users)`` from the first registration's month to ``now``'s,
        empty when there are no users.
    """
    rows = await session.execute(
        select(User.created_at).where(User.deleted_at.is_(None)).order_by(User.created_at)
    )
    created = [row for (row,) in rows.all()]
    if not created:
        return []
    series = []
    index = 0
    for month in _months(created[0], now):
        while index < len(created) and f"{created[index]:%Y-%m}" <= month:
            index += 1
        series.append((month, index))
    return series


async def active_users(session: AsyncSession, *, now: datetime) -> ActiveUsers:
    """Count users who signed in, and who saved a dataset, in the last 30 days.

    Args:
        session: Database session.
        now: The current time.

    Returns:
        The two counts.
    """
    since = now - ACTIVE_WINDOW
    return ActiveUsers(
        signed_in=await _count(
            session,
            select(func.count(User.id)).where(
                User.deleted_at.is_(None), User.last_login_at >= since
            ),
        ),
        saved_a_dataset=await _count(
            session,
            select(func.count(func.distinct(DatasetVersion.created_by_id))).where(
                DatasetVersion.created_at >= since, DatasetVersion.created_by_id.is_not(None)
            ),
        ),
    )
