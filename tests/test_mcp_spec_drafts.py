"""The life of a specification draft over MCP: create, import, clone, delete.

A draft belongs to one user, and its name is unique to that user, so every tool
here is scoped by owner rather than by tenant. The building logic is metaseed's
``SpecBuilder``, already shared with the web UI; the hub adds loading, saving
and that scoping, so these assert the draft row in the database.
Editing an existing draft in place is covered in test_mcp_spec_editing.py.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_tenant, make_user
from tests.mcp_helpers import (
    _calling_with,
    _drafting,
    _spec_of,
    _tool,
    _user_with_token,
)


async def _draft_id(name: str) -> str:
    """The row id of a draft, for assertions about state outside the spec."""
    from sqlalchemy import select

    from metaseed_hub.database import db
    from metaseed_hub.models import SpecDraft

    async with db.session_factory() as check:
        draft = (await check.execute(select(SpecDraft).where(SpecDraft.name == name))).scalar_one()
        return draft.id


class TestSpecTools:
    """Building a specification through the agent.

    The logic is metaseed's ``SpecBuilder``, already shared with the web UI; the
    hub adds loading and saving. So these assert the draft in the database
    actually changed, which is the only part the hub is responsible for.
    """

    async def test_a_draft_is_created(self, server, session: AsyncSession) -> None:
        await _drafting(server, session, slug="spec0001", name="MyProfile")

        spec = await _spec_of("MyProfile")
        assert spec["name"] == "MyProfile"
        assert spec["version"] == "1.0"

    async def test_entities_and_fields_are_added(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="spec0002", name="Built")

        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Built", "Study", "One study")
            await add_field("Built", "Study", "title", "string", required=True)

        spec = await _spec_of("Built")
        assert "Study" in spec["entities"]
        fields = spec["entities"]["Study"]["fields"]
        assert [f["name"] for f in fields] == ["title"]
        assert fields[0]["required"] is True

    async def test_the_root_entity_can_be_set(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="spec0003", name="Rooted")

        add_entity = await _tool(server, "spec_add_entity")
        set_root = await _tool(server, "spec_set_root_entity")
        with _calling_with(secret):
            await add_entity("Rooted", "Investigation")
            await set_root("Rooted", "Investigation")

        assert (await _spec_of("Rooted"))["root_entity"] == "Investigation"

    async def test_validation_reports_metaseeds_answer_unchanged(
        self, server, session: AsyncSession
    ) -> None:
        """Whatever ``SpecBuilder.validate`` says, verbatim.

        A spec with no entities validates clean — metaseed does not treat that
        as a problem. The hub must not invent a rule of its own here: a second
        source of truth about what makes a spec valid would drift from the one
        that actually builds the models.
        """
        from metaseed.specs.builder import SpecBuilder

        secret = await _drafting(server, session, slug="spec0004", name="Empty")

        validate = await _tool(server, "spec_validate")
        with _calling_with(secret):
            result = json.loads(await validate("Empty"))

        assert result["problems"] == SpecBuilder.empty("Empty", "1.0").validate()
        assert result["valid"] is True

    async def test_a_bad_edit_is_refused_by_metaseed(self, server, session: AsyncSession) -> None:
        """Rejections come from SpecBuilder too, and reach the agent as errors
        rather than being silently swallowed."""
        secret = await _drafting(server, session, slug="spec0008", name="Strict")

        set_root = await _tool(server, "spec_set_root_entity")
        with _calling_with(secret), pytest.raises(ValueError, match="not found"):
            await set_root("Strict", "NoSuchEntity")

    async def test_yaml_can_be_previewed(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="spec0005", name="Yamly")

        add_entity = await _tool(server, "spec_add_entity")
        preview = await _tool(server, "spec_preview_yaml")
        with _calling_with(secret):
            await add_entity("Yamly", "Study")
            result = json.loads(await preview("Yamly"))

        assert "Study" in result["yaml"]
        assert "name: Yamly" in result["yaml"]

    async def test_an_agent_cannot_publish(self, server) -> None:
        """Publishing shares a specification with every user of the hub, so it
        stays a human action taken in the web interface."""
        names = {t.name for t in await server.list_tools()}

        assert not any("publish" in n for n in names), (
            f"no tool may publish: {[n for n in names if 'publish' in n]}"
        )

    async def test_another_users_draft_is_not_editable(self, server, session: AsyncSession) -> None:
        secret_a = await _drafting(server, session, slug="spec0006", name="Mine")
        await _drafting(server, session, slug="spec0007", name="Theirs")

        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret_a), pytest.raises(ValueError, match="No specification draft"):
            await add_entity("Theirs", "Sneaky")


_ROUND_TRIP_YAML = """
name: RoundTrip
version: "1.0"
display_name: Round Trip
description: An imported specification
root_entity: Study
entities:
  Study:
    description: A study of things
    fields:
      - name: title
        type: string
        required: true
      - name: samples
        type: list
        items: Sample
  Sample:
    description: A sample
    fields:
      - name: label
        type: string
"""


class TestSpecImportYaml:
    """A YAML document becomes a private draft, the way the web Import page
    already does it — same parser, same draft row, same name-uniqueness."""

    async def test_an_imported_draft_round_trips_through_the_preview(
        self, server, session: AsyncSession
    ) -> None:
        """Import then preview must describe the same specification: nothing
        gained, nothing lost, or the hub's copy is not the user's spec."""
        from metaseed_hub.ui.spec_builder_helpers import parse_spec_from_yaml

        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00001", email="imp00001@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        preview = await _tool(server, "spec_preview_yaml")
        with _calling_with(secret):
            created = json.loads(await import_yaml(_ROUND_TRIP_YAML))
            assert created["name"] == "RoundTrip", "an empty name falls back to the spec's own"
            assert created["version"] == "1.0"
            previewed = json.loads(await preview("RoundTrip"))

        assert parse_spec_from_yaml(previewed["yaml"]) == parse_spec_from_yaml(_ROUND_TRIP_YAML)

    async def test_the_draft_name_argument_wins_over_the_specs_own(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00002", email="imp00002@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret):
            created = json.loads(await import_yaml(_ROUND_TRIP_YAML, "Renamed Import"))

        assert created["name"] == "Renamed Import"
        spec = await _spec_of("Renamed Import")
        assert spec["name"] == "RoundTrip", "the draft name does not rewrite the spec"

    async def test_a_name_collision_is_a_clean_error(self, server, session: AsyncSession) -> None:
        """Same rule as spec_create: draft names are unique per user."""
        secret = await _drafting(server, session, slug="imp00003", name="RoundTrip")
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret), pytest.raises(ValueError, match="already exists"):
            await import_yaml(_ROUND_TRIP_YAML)

    async def test_invalid_yaml_is_a_clean_error(self, server, session: AsyncSession) -> None:
        """A syntax error reads as a ValueError the agent can act on, not a
        stack trace."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00004", email="imp00004@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret), pytest.raises(ValueError, match="Invalid YAML"):
            await import_yaml("entities: [unclosed")

    async def test_a_non_mapping_document_is_refused(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="imp00005", email="imp00005@example.org"
        )
        import_yaml = await _tool(server, "spec_import_yaml")
        with _calling_with(secret), pytest.raises(ValueError, match="mapping"):
            await import_yaml("- just\n- a list")

    async def test_an_imported_draft_is_scoped_to_its_importer(
        self, server, session: AsyncSession
    ) -> None:
        """Another user of the same tenant can neither see nor edit it."""
        from metaseed_hub.tokens import issue_token
        from tests.factories import make_tenant, make_user

        tenant = make_tenant(slug="imp00006")
        session.add(tenant)
        await session.flush()
        user_a = make_user(tenant=tenant, email="imp00006-a@example.org")
        user_b = make_user(tenant=tenant, email="imp00006-b@example.org")
        session.add_all([user_a, user_b])
        await session.commit()
        secret_a, _ = await issue_token(session, user_a, name="agent")
        secret_b, _ = await issue_token(session, user_b, name="agent")

        import_yaml = await _tool(server, "spec_import_yaml")
        listing = await _tool(server, "list_spec_drafts")
        status = await _tool(server, "spec_status")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret_a):
            await import_yaml(_ROUND_TRIP_YAML, "OnlyMine")
        with _calling_with(secret_b):
            assert json.loads(await listing()) == [], "not visible in the other user's list"
            with pytest.raises(ValueError, match="No specification draft"):
                await status("OnlyMine")
            with pytest.raises(ValueError, match="No specification draft"):
                await add_entity("OnlyMine", "Intruder")


class TestSpecClone:
    """A built-in profile or published specification becomes a private draft."""

    async def test_a_built_in_profile_can_be_cloned(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00001", email="clo00001@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret):
            created = json.loads(await clone("miappe", "1.1", "MyMiappe"))

        assert created["name"] == "MyMiappe"
        spec = await _spec_of("MyMiappe")
        assert spec["root_entity"] == "Investigation"
        assert "Study" in spec["entities"]

    async def test_the_draft_name_defaults_to_the_profiles_own(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00002", email="clo00002@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret):
            created = json.loads(await clone("miappe", "1.1"))

        assert created["name"] == "miappe"

    async def test_a_cloned_draft_is_editable_in_place(self, server, session: AsyncSession) -> None:
        """The clone is the caller's own copy, so edits land in the draft."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00003", email="clo00003@example.org"
        )
        clone = await _tool(server, "spec_clone")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret):
            await clone("miappe", "1.1", "Extended")
            await add_entity("Extended", "Drone", "An aerial platform")

        spec = await _spec_of("Extended")
        assert spec["entities"]["Drone"]["description"] == "An aerial platform"

    async def test_an_unknown_version_lists_the_available_ones(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00004", email="clo00004@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret), pytest.raises(ValueError, match=r"available.*1\.1"):
            await clone("miappe", "99.9")

    async def test_an_unknown_profile_points_at_list_profiles(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="clo00005", email="clo00005@example.org"
        )
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret), pytest.raises(ValueError, match="list_profiles"):
            await clone("no-such-profile", "1.0")

    async def test_a_name_collision_is_a_clean_error(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="clo00006", name="miappe")
        clone = await _tool(server, "spec_clone")
        with _calling_with(secret), pytest.raises(ValueError, match="already exists"):
            await clone("miappe", "1.1")


class TestSpecDeleteDraft:
    """A draft the caller no longer wants is removable without the web UI.

    Every other draft mutation is available here; without this one an agent can
    only accumulate drafts it cannot clear.
    """

    async def test_a_draft_is_deleted(self, server, session: AsyncSession) -> None:
        from metaseed_hub.ui.spec_builder.cache import state_cache

        secret = await _drafting(server, session, slug="del00001", name="Disposable")
        draft_id = await _draft_id("Disposable")
        delete = await _tool(server, "spec_delete_draft")
        listing = await _tool(server, "list_spec_drafts")
        with _calling_with(secret):
            result = json.loads(await delete("Disposable"))
            assert result == {"deleted": "Disposable"}
            assert json.loads(await listing()) == []

        assert draft_id not in state_cache, "a deleted draft must not stay cached"

    async def test_a_deleted_draft_is_no_longer_editable(
        self, server, session: AsyncSession
    ) -> None:
        secret = await _drafting(server, session, slug="del00002", name="Gone")
        delete = await _tool(server, "spec_delete_draft")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret):
            await delete("Gone")
            with pytest.raises(ValueError, match="No specification draft"):
                await add_entity("Gone", "Study")

    async def test_another_users_draft_is_not_deletable(
        self, server, session: AsyncSession
    ) -> None:
        """Scoped like every other draft tool: a name someone else owns reads as
        absent, not as reachable."""
        from metaseed_hub.tokens import issue_token
        from tests.factories import make_tenant, make_user

        tenant = make_tenant(slug="del00003")
        session.add(tenant)
        await session.flush()
        user_a = make_user(tenant=tenant, email="del00003-a@example.org")
        user_b = make_user(tenant=tenant, email="del00003-b@example.org")
        session.add_all([user_a, user_b])
        await session.commit()
        secret_a, _ = await issue_token(session, user_a, name="agent")
        secret_b, _ = await issue_token(session, user_b, name="agent")

        create = await _tool(server, "spec_create")
        delete = await _tool(server, "spec_delete_draft")
        with _calling_with(secret_a):
            await create("Guarded", "1.0")
        with _calling_with(secret_b), pytest.raises(ValueError, match="No specification draft"):
            await delete("Guarded")

        assert (await _spec_of("Guarded"))["name"] == "Guarded", "the owner still has it"

    async def test_an_unknown_name_is_a_clean_error(self, server, session: AsyncSession) -> None:
        secret = await _drafting(server, session, slug="del00004", name="Kept")
        delete = await _tool(server, "spec_delete_draft")
        with _calling_with(secret), pytest.raises(ValueError, match="No specification draft"):
            await delete("NeverExisted")

    async def test_a_draft_a_dataset_depends_on_is_kept(
        self, server, session: AsyncSession
    ) -> None:
        """Deleting it would leave the dataset without a specification, so the
        agent is told which datasets to deal with first."""
        from sqlalchemy import select

        from metaseed_hub.models import SpecDraft
        from tests.factories import make_dataset

        tenant, _user, secret, _token = await _user_with_token(
            session, slug="del00005", email="del00005@example.org"
        )
        create = await _tool(server, "spec_create")
        with _calling_with(secret):
            await create("InUse", "1.0")

        draft = (
            await session.execute(select(SpecDraft).where(SpecDraft.name == "InUse"))
        ).scalar_one()
        dataset = make_dataset(tenant=tenant, name="dependent", profile="inuse", version="1.0")
        dataset.spec_draft_id = draft.id
        session.add(dataset)
        await session.commit()

        delete = await _tool(server, "spec_delete_draft")
        with _calling_with(secret), pytest.raises(ValueError, match="dependent"):
            await delete("InUse")

        assert (await _spec_of("InUse"))["name"] == "InUse", "the draft survived"


class TestDraftScoping:
    """Draft names are unique per user, so lookups must be user-scoped.

    A tenant-wide lookup would let two same-named drafts in one tenant crash
    the query — or let one user edit another's draft.
    """

    async def _two_users_one_tenant(self, session: AsyncSession):
        from metaseed_hub.tokens import issue_token

        tenant = make_tenant(slug="draftscope")
        session.add(tenant)
        await session.flush()
        alice = make_user(tenant=tenant, email="alice-drafts@example.org")
        bob = make_user(tenant=tenant, email="bob-drafts@example.org")
        session.add_all([alice, bob])
        await session.commit()
        secret_a, _ = await issue_token(session, alice, name="agent")
        secret_b, _ = await issue_token(session, bob, name="agent")
        return secret_a, secret_b

    async def test_two_users_may_hold_the_same_draft_name(
        self, server, session: AsyncSession
    ) -> None:
        secret_a, secret_b = await self._two_users_one_tenant(session)

        create = await _tool(server, "spec_create")
        with _calling_with(secret_a):
            await create("Twin", "1.0")
        with _calling_with(secret_b):
            # Unique per user, not per tenant: this must not be refused.
            await create("Twin", "1.0")

    async def test_a_users_edit_lands_in_their_own_draft(
        self, server, session: AsyncSession
    ) -> None:
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import SpecDraft

        secret_a, secret_b = await self._two_users_one_tenant(session)

        create = await _tool(server, "spec_create")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret_a):
            await create("Twin", "1.0")
        with _calling_with(secret_b):
            await create("Twin", "1.0")
            await add_entity("Twin", "OnlyBobs")

        async with db.session_factory() as check:
            drafts = (
                (await check.execute(select(SpecDraft).where(SpecDraft.name == "Twin")))
                .scalars()
                .all()
            )
        entities_by_draft = sorted(
            list((d.spec_data.get("spec") or {}).get("entities", {})) for d in drafts
        )
        assert entities_by_draft == [[], ["OnlyBobs"]], "the edit must touch only Bob's draft"

    async def test_a_same_tenant_users_draft_is_not_editable(
        self, server, session: AsyncSession
    ) -> None:
        """Tenant scoping alone would make these drafts mutually writable."""
        secret_a, secret_b = await self._two_users_one_tenant(session)

        create = await _tool(server, "spec_create")
        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret_a):
            await create("Private", "1.0")
        with _calling_with(secret_b), pytest.raises(ValueError, match="No specification draft"):
            await add_entity("Private", "Sneaky")
