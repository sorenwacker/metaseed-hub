"""Reading a user's group membership from their identity provider.

Membership comes from the IdP and is never stored here: Keycloak in development,
SRAM in production, with the dev Keycloak configured to emit what SRAM emits so
both are one code path with a different issuer. Persisting the list would create
a copy that goes stale between logins.

SRAM puts membership in the ``eduperson_entitlement`` claim, as URNs shaped::

    urn:mace:surf.nl:sram:group:{organisation}:{collaboration}:{group}
    urn:mace:surf.nl:sram:group:tudelft:sramdemo:sramdemogroup

Which features a group may use is *not* here — that is hub state, because
neither Keycloak nor SRAM models "feature X enabled".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable

#: The prefix every SRAM group entitlement carries. An identity provider may
#: also send entitlements that have nothing to do with group membership, so the
#: prefix is what distinguishes ours rather than the claim being ours alone.
SRAM_GROUP_PREFIX = "urn:mace:surf.nl:sram:group:"

#: The OIDC claim carrying entitlements.
ENTITLEMENT_CLAIM = "eduperson_entitlement"


class SramGroup(NamedTuple):
    """One SRAM group entitlement, split into its parts.

    ``urn`` is kept because it is what a grant is matched against; the parts are
    for display and for granting a whole collaboration at once.
    """

    urn: str
    organisation: str
    collaboration: str
    group: str


def group_urns(entitlements: Iterable[str] | None) -> list[str]:
    """The SRAM group URNs among ``entitlements``, in the order given.

    Args:
        entitlements: The raw ``eduperson_entitlement`` values from a verified
            token (:attr:`metaseed_hub.auth.TokenUser.entitlements`), or
            ``None`` when unauthenticated.

    Returns:
        Every entitlement carrying :data:`SRAM_GROUP_PREFIX`. Entitlements that
        are not group membership are dropped rather than raising -- an IdP is
        free to send others, and rejecting them would break login for a reason
        that has nothing to do with this feature.
    """
    if not entitlements:
        return []
    return [
        value
        for value in entitlements
        if isinstance(value, str) and value.startswith(SRAM_GROUP_PREFIX)
    ]


def parse_group(urn: str) -> SramGroup | None:
    """Split a SRAM group URN into its parts, or ``None`` if it is not one.

    Returns ``None`` rather than raising for anything unrecognised: the caller
    is filtering a list an identity provider controls, not validating input the
    hub produced.
    """
    if not urn.startswith(SRAM_GROUP_PREFIX):
        return None
    remainder = urn[len(SRAM_GROUP_PREFIX) :]
    parts = remainder.split(":")
    if len(parts) != 3 or not all(parts):
        return None
    organisation, collaboration, group = parts
    return SramGroup(urn, organisation, collaboration, group)


def groups(entitlements: Iterable[str] | None) -> list[SramGroup]:
    """Every well-formed SRAM group among ``entitlements``.

    A URN carrying the prefix but not the three expected parts is dropped, so a
    malformed entitlement cannot become a group that no grant will ever match
    while looking like one in the UI.
    """
    parsed = (parse_group(urn) for urn in group_urns(entitlements))
    return [group for group in parsed if group is not None]


def collaboration_urn(group: SramGroup) -> str:
    """The URN standing for *every* group in ``group``'s collaboration.

    Lets a grant cover a whole collaboration without naming each of its groups.
    Deliberately the same shape as a group URN with the group part omitted, so
    the two can be matched by the same equality check.
    """
    return f"{SRAM_GROUP_PREFIX}{group.organisation}:{group.collaboration}"


def entitled_urns(entitlements: Iterable[str] | None) -> set[str]:
    """Everything a grant may be matched against for this user.

    Each group contributes both its own URN and its collaboration's, so a grant
    can be written at either level and matched by plain set membership.
    """
    urns: set[str] = set()
    for group in groups(entitlements):
        urns.add(group.urn)
        urns.add(collaboration_urn(group))
    return urns
