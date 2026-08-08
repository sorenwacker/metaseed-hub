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


def _claims(*entitlements: str) -> dict:
    return {"eduperson_entitlement": list(entitlements)}


class TestReadingTheClaim:
    def test_group_urns_are_taken_from_the_entitlement_claim(self) -> None:
        assert group_urns(_claims(TUDELFT, ADMINS)) == [TUDELFT, ADMINS]

    def test_an_unrelated_entitlement_is_ignored_not_rejected(self) -> None:
        # An identity provider is free to send entitlements that have nothing to
        # do with group membership. Raising would break login for a reason
        # unconnected to this feature.
        claims = _claims("urn:mace:terena.org:tcs:personal-user", TUDELFT)
        assert group_urns(claims) == [TUDELFT]

    def test_a_single_valued_claim_arrives_as_a_string(self) -> None:
        assert group_urns({"eduperson_entitlement": TUDELFT}) == [TUDELFT]

    def test_no_claim_and_no_session_yield_nothing(self) -> None:
        assert group_urns(None) == []
        assert group_urns({}) == []
        assert group_urns({"eduperson_entitlement": None}) == []

    def test_a_non_string_entry_does_not_crash_the_read(self) -> None:
        assert group_urns({"eduperson_entitlement": [None, 7, TUDELFT]}) == [TUDELFT]


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
        assert groups(_claims(f"{SRAM_GROUP_PREFIX}tudelft:sramdemo")) == []


class TestWhatAGrantIsMatchedAgainst:
    def test_a_users_own_group_is_matchable(self) -> None:
        assert TUDELFT in entitled_urns(_claims(TUDELFT))

    def test_the_collaboration_is_matchable_too(self) -> None:
        # So a grant can cover a whole collaboration without naming each of its
        # groups, checked by the same equality as a group-level grant.
        assert f"{SRAM_GROUP_PREFIX}tudelft:sramdemo" in entitled_urns(_claims(TUDELFT))

    def test_a_different_collaboration_is_not_matchable(self) -> None:
        entitled = entitled_urns(_claims(TUDELFT))
        assert ADMINS not in entitled
        assert f"{SRAM_GROUP_PREFIX}myorg:myco" not in entitled

    def test_a_user_in_no_group_matches_nothing(self) -> None:
        assert entitled_urns(_claims()) == set()
        assert entitled_urns(None) == set()

    def test_the_collaboration_urn_is_shaped_like_a_group_urn(self) -> None:
        parsed = parse_group(TUDELFT)
        assert parsed is not None
        assert collaboration_urn(parsed).startswith(SRAM_GROUP_PREFIX)
