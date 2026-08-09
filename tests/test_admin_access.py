"""Who gets admin, and where the answer comes from.

Admin arrives two ways: a Keycloak realm role named ``admin`` in development,
or membership of an SRAM group whose URN ends ``:admin`` in production. These
must be two explicit checks on two explicit sources -- when both were read from
one ``roles`` list filled by a fallback, admin meant a different thing per
issuer, and which meaning applied depended on whether the other claim happened
to be empty.
"""

from __future__ import annotations

import pytest

from metaseed_hub.auth import TokenUser
from metaseed_hub.ui.routes.admin import is_admin


@pytest.fixture(autouse=True)
def _bare_admin_role(monkeypatch: pytest.MonkeyPatch):
    """Pin the default configuration; the environment may override it."""
    monkeypatch.setattr("metaseed_hub.ui.routes.admin.get_admin_role", lambda: "admin")


def _user(roles: list[str] | None = None, entitlements: list[str] | None = None):
    return TokenUser(
        sub="s",
        email="e@example.org",
        name="n",
        roles=roles or [],
        entitlements=entitlements or [],
    )


class TestRealmRoleAdmin:
    def test_the_keycloak_admin_role_grants_admin(self) -> None:
        assert is_admin(_user(roles=["admin"]))

    def test_an_ordinary_realm_role_does_not(self) -> None:
        assert not is_admin(_user(roles=["user", "offline_access"]))


class TestSramGroupAdmin:
    def test_an_admin_group_grants_admin(self) -> None:
        assert is_admin(_user(entitlements=["urn:mace:surf.nl:sram:group:tudelft:metaseed:admin"]))

    def test_it_grants_admin_even_when_realm_roles_are_present(self) -> None:
        # The old fallback read entitlements only when roles was empty, so an
        # SRAM admin stopped being one the moment the IdP added any realm role.
        assert is_admin(
            _user(
                roles=["offline_access"],
                entitlements=["urn:mace:surf.nl:sram:group:tudelft:metaseed:admin"],
            )
        )

    def test_an_ordinary_group_does_not(self) -> None:
        assert not is_admin(
            _user(entitlements=["urn:mace:surf.nl:sram:group:tudelft:metaseed:beta-testers"])
        )

    def test_a_group_merely_containing_admin_does_not(self) -> None:
        assert not is_admin(
            _user(entitlements=["urn:mace:surf.nl:sram:group:tudelft:metaseed:admins"])
        )


class TestRolesAreNotEntitlements:
    def test_roles_no_longer_holds_entitlements_when_realm_roles_are_absent(
        self,
    ) -> None:
        # The fallback made roles mean "realm role in dev, group URN in prod".
        # Membership now has its own field, read unconditionally.
        from metaseed_hub.auth import _entitlement_list

        payload = {"eduperson_entitlement": ["urn:mace:surf.nl:sram:group:tudelft:metaseed:admin"]}
        assert _entitlement_list(payload) == payload["eduperson_entitlement"]

    def test_an_entitlement_in_roles_does_not_grant_admin(self) -> None:
        # If a stale token or misconfigured mapper puts URNs into roles, that
        # must not be an alternative road to admin.
        assert not is_admin(_user(roles=["urn:mace:surf.nl:sram:group:tudelft:metaseed:admin"]))


class TestUrnConfiguredAdmin:
    """Production sets ``admin_role`` to the full SRAM group URN."""

    URN = "urn:mace:surf.nl:sram:group:tudelft:metaseed:admin"

    @pytest.fixture(autouse=True)
    def _urn_admin_role(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("metaseed_hub.ui.routes.admin.get_admin_role", lambda: self.URN)

    def test_the_configured_group_grants_admin(self) -> None:
        assert is_admin(_user(entitlements=[self.URN]))

    def test_the_bare_realm_role_no_longer_does(self) -> None:
        # When admin is a specific group, a realm role named "admin" from some
        # other realm configuration must not be an alternative road in.
        assert not is_admin(_user(roles=["admin"]))

    def test_the_urn_in_roles_does_not_count(self) -> None:
        assert not is_admin(_user(roles=[self.URN]))
