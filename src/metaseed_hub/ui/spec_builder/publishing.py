"""Choosing who a specification is published to.

Publishing used to mean visible to every user of the hub. It now asks for an
audience, and this is the one place that turns what the form sent into the
value stored on the specification, so the editor and any other publish path
cannot disagree about what an empty choice means.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from metaseed_hub.audience import EVERYONE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def audience_for_publisher(
    session: AsyncSession, user_id: str, chosen: str | None
) -> str | None:
    """The audience to store, once the publisher is known to be in it.

    Args:
        session: Database session.
        user_id: The person publishing.
        chosen: What the form sent: a collaboration URN, or empty for everyone.

    Returns:
        The URN to store, or :data:`~metaseed_hub.audience.EVERYONE`.

    Raises:
        metaseed_hub.collaborations.NotInCollaborationError: If ``chosen`` is
            one of a collaboration's groups, which a release is never
            addressed to, or a collaboration the publisher's recorded
            membership does not put them in.
    """
    if not chosen:
        return EVERYONE
    from metaseed_hub.collaborations import (
        NotInCollaborationError,
        collaboration_urns_of,
        short_name,
    )
    from metaseed_hub.entitlements import parse_group

    if parse_group(chosen) is not None:
        raise NotInCollaborationError(
            chosen,
            reason=(
                f"{short_name(chosen)} is a group. A specification is published "
                "to a collaboration or to the whole hub; share it with a group "
                "instead."
            ),
        )
    if chosen not in await collaboration_urns_of(session, user_id):
        raise NotInCollaborationError(chosen)
    return chosen
