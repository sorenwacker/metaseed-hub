"""Reading group membership from the identity provider's claims.

Membership comes from the IdP -- Keycloak in development, SRAM in production --
and the dev Keycloak is configured to emit what SRAM emits, so these tests are
written against SRAM's claim shape and hold for both.
"""

from __future__ import annotations

from metaseed_hub.entitlements import (
    SRAM_GROUP_PREFIX,
    collaboration_urn,
    entitled_urns,
    group_urns,
    groups,
    parse_group,
)

TUDELFT = f"{SRAM_GROUP_PREFIX}tudelft:sramdemo:sramdemogroup"
ADMINS = f"{SRAM_GROUP_PREFIX}myorg:myco:admins"


def _entitlements(*urns: str) -> list[str]:
    """The raw claim values a verified token carries."""
    return list(urns)


class TestReadingTheClaim:
    def test_group_urns_are_taken_from_the_entitlement_claim(self) -> None:
        assert group_urns(_entitlements(TUDELFT, ADMINS)) == [TUDELFT, ADMINS]

    def test_an_unrelated_entitlement_is_ignored_not_rejected(self) -> None:
        # An identity provider is free to send entitlements that have nothing to
        # do with group membership. Raising would break login for a reason
        # unconnected to this feature.
        entitlements = _entitlements("urn:mace:terena.org:tcs:personal-user", TUDELFT)
        assert group_urns(entitlements) == [TUDELFT]

    def test_nothing_in_hand_yields_nothing(self) -> None:
        assert group_urns(None) == []
        assert group_urns([]) == []

    def test_a_non_string_entry_does_not_crash_the_read(self) -> None:
        assert group_urns([None, 7, TUDELFT]) == [TUDELFT]  # type: ignore[list-item]


class TestParsing:
    def test_a_group_urn_splits_into_organisation_collaboration_and_group(
        self,
    ) -> None:
        parsed = parse_group(TUDELFT)
        assert parsed is not None
        assert (parsed.organisation, parsed.collaboration, parsed.group) == (
            "tudelft",
            "sramdemo",
            "sramdemogroup",
        )
        assert parsed.urn == TUDELFT

    def test_something_that_is_not_a_group_urn_is_not_one(self) -> None:
        assert parse_group("urn:mace:terena.org:tcs:personal-user") is None

    def test_a_urn_missing_a_part_is_dropped_rather_than_half_parsed(self) -> None:
        # A malformed entitlement must not become a group that no grant will
        # ever match while still appearing in the UI as though it could.
        assert parse_group(f"{SRAM_GROUP_PREFIX}tudelft:sramdemo") is None
        assert parse_group(f"{SRAM_GROUP_PREFIX}tudelft::group") is None
        assert groups(_entitlements(f"{SRAM_GROUP_PREFIX}tudelft:sramdemo")) == []


class TestWhatAGrantIsMatchedAgainst:
    def test_a_users_own_group_is_matchable(self) -> None:
        assert TUDELFT in entitled_urns(_entitlements(TUDELFT))

    def test_the_collaboration_is_matchable_too(self) -> None:
        # So a grant can cover a whole collaboration without naming each of its
        # groups, checked by the same equality as a group-level grant.
        assert f"{SRAM_GROUP_PREFIX}tudelft:sramdemo" in entitled_urns(_entitlements(TUDELFT))

    def test_a_different_collaboration_is_not_matchable(self) -> None:
        entitled = entitled_urns(_entitlements(TUDELFT))
        assert ADMINS not in entitled
        assert f"{SRAM_GROUP_PREFIX}myorg:myco" not in entitled

    def test_a_user_in_no_group_matches_nothing(self) -> None:
        assert entitled_urns(_entitlements()) == set()
        assert entitled_urns(None) == set()

    def test_the_collaboration_urn_is_shaped_like_a_group_urn(self) -> None:
        parsed = parse_group(TUDELFT)
        assert parsed is not None
        assert collaboration_urn(parsed).startswith(SRAM_GROUP_PREFIX)


class TestReadingTheClaimOffAToken:
    """Claim shape is handled where the token is verified, not downstream.

    ``TokenUser.entitlements`` is always a list, so nothing further along has to
    know that a single-valued OIDC claim arrives as a bare string.
    """

    def test_a_single_valued_claim_arrives_as_a_string(self) -> None:
        from metaseed_hub.auth import _entitlement_list

        assert _entitlement_list({"eduperson_entitlement": TUDELFT}) == [TUDELFT]

    def test_a_missing_claim_is_no_membership_not_an_error(self) -> None:
        from metaseed_hub.auth import _entitlement_list

        assert _entitlement_list({}) == []
        assert _entitlement_list({"eduperson_entitlement": None}) == []

    def test_entitlements_are_not_taken_from_roles(self) -> None:
        # `roles` means a Keycloak realm role in dev and a SRAM group URN in
        # production; reading membership from it would behave differently per
        # issuer, which is what configuring dev Keycloak to match SRAM avoids.
        from metaseed_hub.auth import _entitlement_list

        assert _entitlement_list({"realm_access": {"roles": [TUDELFT]}}) == []
