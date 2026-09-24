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
        chosen: What the form sent: a collaboration or group URN, or empty for
            everyone.

    Returns:
        The URN to store, or :data:`~metaseed_hub.audience.EVERYONE`.

    Raises:
        metaseed_hub.collaborations.NotInCollaborationError: If the publisher's
            recorded membership does not put them in ``chosen``. Publishing to
            a group one cannot see is refused for the same reason granting to
            one is.
    """
    if not chosen:
        return EVERYONE
    from metaseed_hub.collaborations import NotInCollaborationError, entitled_urns_of

    if chosen not in await entitled_urns_of(session, user_id):
        raise NotInCollaborationError(chosen)
    return chosen
