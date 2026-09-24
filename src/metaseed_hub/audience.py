"""Who may see a published specification.

Publishing meant visible to every user of the hub, which left a working group
no way to release a specification to itself. A specification now carries an
audience: ``NULL`` for everyone, or a SRAM collaboration or group URN for its
members. Every surface that lists a published specification reads this one
rule, because a specification hidden on one page and offered by another is not
hidden at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import or_

from metaseed_hub.models import Spec

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

#: The audience of a specification every user of the hub may see. Stored as
#: NULL: an absent restriction, rather than a URN standing for "all".
EVERYONE: str | None = None


def visible_to(urns: Iterable[str]) -> Any:
    """The condition selecting specifications ``urns`` may see.

    Args:
        urns: Every URN the person is entitled to, both their groups and the
            collaborations those sit in.

    Returns:
        A SQLAlchemy condition, for adding to any query over :class:`Spec`.
    """
    wanted = list(urns)
    if not wanted:
        return Spec.audience_urn.is_(None)
    return or_(Spec.audience_urn.is_(None), Spec.audience_urn.in_(wanted))


async def visible_specs(session: AsyncSession, user_id: str | None) -> Any:
    """The condition selecting the specifications ``user_id`` may see.

    Reads the membership recorded at the person's last sign-in, so someone
    who has left a collaboration loses sight of its specifications once that
    record goes stale, without anyone acting.

    Args:
        session: Database session.
        user_id: The person asking, or ``None`` when nobody is signed in, who
            sees only what everyone sees.
    """
    if user_id is None:
        return visible_to(())
    from metaseed_hub.collaborations import entitled_urns_of

    return visible_to(await entitled_urns_of(session, user_id))


def audience_label(urn: str | None) -> str:
    """How an audience is named in the interface."""
    if urn is None:
        return "Everyone"
    from metaseed_hub.collaborations import grant_label

    return grant_label(urn)
