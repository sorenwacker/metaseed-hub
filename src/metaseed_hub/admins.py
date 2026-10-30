"""Who the hub's administrators are.

Membership of the SRAM admin group grants admin, read from a token at request
time. Telling the admins of something outside any request, such as an error,
needs the same rule over the memberships recorded at sign-in instead, so the
rule lives here, below both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from metaseed_hub.collaborations import users_in_groups
from metaseed_hub.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession


def get_admin_role() -> str:
    """The admin group, as the setting names it (default: ``admin``)."""
    return get_settings().admin_role


def grants_admin(urns: Iterable[str]) -> bool:
    """Whether any of ``urns`` is the admin group.

    The setting names the group either as the full URN, matched exactly, which
    is how production configures it, or as a bare name matched as the URN's
    group part (``...:admin`` / ``...#admin``).

    Args:
        urns: Group URNs, from a token or from the recorded memberships.
    """
    admin_role = get_admin_role()
    if ":" in admin_role:
        return admin_role in set(urns)
    return any(urn.endswith((f":{admin_role}", f"#{admin_role}")) for urn in urns)


async def admin_ids(session: AsyncSession) -> list[str]:
    """Every user whose recorded, still fresh membership includes the admin group."""
    return await users_in_groups(session, lambda urn: grants_admin([urn]))
