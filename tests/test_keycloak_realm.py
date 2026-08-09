"""The dev realm must emit what production emits.

Group membership comes from the identity provider: Keycloak in development, SRAM
in production. That is only one code path if the dev Keycloak is configured to
produce SRAM's claim in SRAM's shape -- otherwise the feature works in dev and
fails on deployment, which is the failure this file exists to prevent.

Asserted against the committed realm config rather than a running Keycloak, so
it holds in CI and on a machine that has never started the stack.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaseed_hub.entitlements import SRAM_GROUP_PREFIX, parse_group

REALM = Path(__file__).parent.parent / "docker" / "keycloak-realm.json"


@pytest.fixture(scope="module")
def realm() -> dict:
    return json.loads(REALM.read_text())


@pytest.fixture(scope="module")
def hub_client(realm: dict) -> dict:
    return next(c for c in realm["clients"] if c["clientId"] == "metaseed-hub")


@pytest.fixture(scope="module")
def entitlement_mapper(hub_client: dict) -> dict:
    mappers = hub_client.get("protocolMappers", [])
    matching = [m for m in mappers if m["config"].get("claim.name") == "eduperson_entitlement"]
    assert len(matching) == 1, "exactly one mapper should produce the claim"
    return matching[0]


class TestTheClaim:
    def test_the_claim_is_the_one_sram_sends(self, entitlement_mapper: dict) -> None:
        assert entitlement_mapper["config"]["claim.name"] == "eduperson_entitlement"

    def test_it_reaches_the_id_token(self, entitlement_mapper: dict) -> None:
        # verify_token decodes the ID token; a claim only on the userinfo
        # endpoint would never be seen.
        assert entitlement_mapper["config"]["id.token.claim"] == "true"

    def test_group_names_are_emitted_without_a_path_prefix(self, entitlement_mapper: dict) -> None:
        # Keycloak prefixes group paths with "/" unless told otherwise, which
        # would make every URN unparseable.
        assert entitlement_mapper["config"]["full.path"] == "false"


class TestTheGroups:
    def test_every_group_is_a_well_formed_sram_urn(self, realm: dict) -> None:
        groups = realm.get("groups", [])
        assert groups, "the dev realm defines no groups"
        for group in groups:
            parsed = parse_group(group["name"])
            assert parsed is not None, f"{group['name']} is not a SRAM group URN"

    def test_the_plugins_each_have_a_group(self, realm: dict) -> None:
        names = {g["name"] for g in realm["groups"]}
        for plugin in ("seek", "ena", "pride", "brapi", "metabolights", "dcat"):
            assert f"{SRAM_GROUP_PREFIX}tudelft:metaseed:{plugin}" in names

    def test_beta_testers_cuts_across_the_plugins(self, realm: dict) -> None:
        names = {g["name"] for g in realm["groups"]}
        assert f"{SRAM_GROUP_PREFIX}tudelft:metaseed:beta-testers" in names

    def test_they_share_one_collaboration(self, realm: dict) -> None:
        # So a single collaboration-level grant can cover all of them.
        collaborations = set()
        for group in realm["groups"]:
            parsed = parse_group(group["name"])
            assert parsed is not None
            collaborations.add((parsed.organisation, parsed.collaboration))
        assert len(collaborations) == 1


class TestTheDevUser:
    def test_the_demo_user_is_in_every_group_except_admin(self, realm: dict) -> None:
        # A fresh dev environment should be able to see every gated feature
        # without anyone hand-editing Keycloak first -- but seeing features and
        # administering the hub are different privileges, so demo is not admin.
        demo = next(u for u in realm["users"] if u["username"] == "demo")
        expected = {f"/{g['name']}" for g in realm["groups"] if not g["name"].endswith(":admin")}
        assert set(demo.get("groups", [])) == expected
