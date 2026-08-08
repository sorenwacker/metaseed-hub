"""Which features a user may use.

Membership comes from the identity provider; the grant is hub state. These
tests exercise the join between them, which is where a mistake would either
hide a feature from someone entitled to it or expose one to someone who is not.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from metaseed_hub.auth import TokenUser
from metaseed_hub.entitlements import SRAM_GROUP_PREFIX
from metaseed_hub.features import enabled_features, has_feature
from metaseed_hub.models import FeatureGrant
from metaseed_hub.ui.dependencies import require_feature

TESTERS = f"{SRAM_GROUP_PREFIX}tudelft:metaseed:beta-testers"
OWNER = f"{SRAM_GROUP_PREFIX}tudelft:metaseed:owner"
COLLABORATION = f"{SRAM_GROUP_PREFIX}tudelft:metaseed"


def _entitlements(*urns: str) -> list[str]:
    """The raw claim values a verified token carries."""
    return list(urns)


async def _grant(session, feature: str, group_urn: str) -> None:
    session.add(FeatureGrant(feature=feature, group_urn=group_urn))
    await session.commit()


class TestResolvingFeatures:
    async def test_a_member_of_a_granted_group_gets_the_feature(self, session) -> None:
        await _grant(session, "seek", TESTERS)
        assert await enabled_features(_entitlements(TESTERS), session) == {"seek"}

    async def test_someone_outside_the_group_gets_nothing(self, session) -> None:
        await _grant(session, "seek", TESTERS)
        assert await enabled_features(_entitlements(OWNER), session) == set()

    async def test_a_user_in_no_group_gets_nothing(self, session) -> None:
        await _grant(session, "seek", TESTERS)
        assert await enabled_features(_entitlements(), session) == set()
        assert await enabled_features(None, session) == set()

    async def test_a_feature_is_off_until_some_group_is_granted_it(self, session) -> None:
        # The default for everybody, which is what makes this safe to deploy.
        assert await enabled_features(_entitlements(TESTERS), session) == set()

    async def test_a_collaboration_grant_covers_its_groups(self, session) -> None:
        # So a whole collaboration can be enabled without naming every group.
        await _grant(session, "seek", COLLABORATION)
        assert await enabled_features(_entitlements(TESTERS), session) == {"seek"}

    async def test_one_group_can_hold_several_features(self, session) -> None:
        await _grant(session, "seek", TESTERS)
        await _grant(session, "restore", TESTERS)
        assert await enabled_features(_entitlements(TESTERS), session) == {"seek", "restore"}

    async def test_a_group_of_one_needs_no_second_mechanism(self, session) -> None:
        # "Only me" is a group with one member, not a per-user grant.
        await _grant(session, "restore", OWNER)
        assert await has_feature("restore", _entitlements(OWNER), session)
        assert not await has_feature("restore", _entitlements(TESTERS), session)


class TestTheRouteGuard:
    """The dependency a route takes in place of ``require_user``."""

    def _user(self, *urns: str) -> TokenUser:
        return TokenUser(
            sub="s", email="e@example.org", name="n", roles=[], entitlements=list(urns)
        )

    async def test_an_entitled_user_passes_through(self, session) -> None:
        await _grant(session, "seek", TESTERS)
        guard = require_feature("seek")
        assert await guard(self._user(TESTERS), session) is not None

    async def test_an_unentitled_user_is_refused(self, session) -> None:
        guard = require_feature("seek")
        with pytest.raises(HTTPException) as raised:
            await guard(self._user(TESTERS), session)
        # 404 rather than 403: a feature someone may not use should not
        # advertise that it exists, which is the point of a beta flag.
        assert raised.value.status_code == 404

    async def test_a_grant_for_another_feature_does_not_open_this_one(self, session) -> None:
        await _grant(session, "restore", TESTERS)
        guard = require_feature("seek")
        with pytest.raises(HTTPException):
            await guard(self._user(TESTERS), session)

    async def test_entitlements_come_from_the_token_not_from_roles(self, session) -> None:
        # `roles` carries a Keycloak realm role in dev and a SRAM group URN in
        # production. Reading membership from it would behave differently per
        # issuer, which is exactly what this design avoids.
        await _grant(session, "seek", TESTERS)
        misleading = TokenUser(
            sub="s", email="e@example.org", name="n", roles=[TESTERS], entitlements=[]
        )
        guard = require_feature("seek")
        with pytest.raises(HTTPException):
            await guard(misleading, session)
