"""The hub's MCP endpoint, and the isolation it has to guarantee.

One process serves every MCP caller, so the failure that matters is a tool
acting as the wrong person -- or as anyone at all without a token. These tests
drive the tool bodies with a token in scope, because that is where the caller is
resolved; a test that called the underlying queries directly would not exercise
the part that can leak.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.mcp import NotAuthenticatedError, create_mcp_server
from metaseed_hub.models import Dataset
from metaseed_hub.tokens import issue_token, revoke_token
from tests.factories import make_dataset, make_tenant, make_user


class _Request:
    """Just the part of a Starlette request the resolver reads."""

    def __init__(self, authorization: str | None) -> None:
        self.headers = {"authorization": authorization} if authorization else {}


@contextmanager
def _calling_with(token: str | None):
    """Serve a tool call as the holder of ``token``.

    Patches the SDK's per-request lookup, which is how a tool body identifies
    its caller under the HTTP transport.
    """
    request = _Request(f"Bearer {token}" if token else None)
    with patch("metaseed.agent.mcp.caller.current_request", lambda: request):
        yield


async def _tool(server, name: str):
    """The registered callable for a tool, by name."""
    tools = await server.list_tools()
    assert any(t.name == name for t in tools), f"{name} is not registered"
    return server._tool_manager.get_tool(name).fn


async def _user_with_token(session: AsyncSession, *, slug: str, email: str):
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=email)
    session.add(user)
    await session.commit()
    secret, token = await issue_token(session, user, name="agent")
    return tenant, user, secret, token


TEST_URL = "postgresql+asyncpg://metaseed:metaseed_dev@localhost:7432/metaseed_hub_test"


@pytest.fixture
async def server(session: AsyncSession):
    """The MCP server, with the app-wide database connected.

    The tools open their own session per call, from ``db``, because each call is
    a different caller. That is the behaviour under test, so the tests connect
    ``db`` rather than substituting the fixture's session.
    """
    from metaseed_hub.database import db

    await db.connect(TEST_URL)
    try:
        yield create_mcp_server()
    finally:
        await db.disconnect()


async def test_the_endpoint_is_mounted_ahead_of_the_hub(server) -> None:
    """/hub matches first if the order is wrong, and the endpoint disappears."""
    from metaseed_hub.main import create_app

    paths = [getattr(r, "path", "") for r in create_app().routes]
    assert "/hub/mcp" in paths
    assert paths.index("/hub/mcp") < paths.index("/hub")


async def test_a_call_without_a_token_is_refused(server, session) -> None:
    """No default caller: the only thing to fall back on is someone else."""
    whoami = await _tool(server, "whoami")

    with _calling_with(None), pytest.raises(NotAuthenticatedError):
        await whoami()


async def test_a_revoked_token_stops_working(server, session: AsyncSession) -> None:
    _t, _u, secret, token = await _user_with_token(session, slug="mcp00001", email="a@example.org")
    await revoke_token(session, token)

    whoami = await _tool(server, "whoami")
    with _calling_with(secret), pytest.raises(NotAuthenticatedError):
        await whoami()


async def test_a_tool_acts_as_the_token_holder(server, session: AsyncSession) -> None:
    """The property the whole design exists for."""
    _ta, _ua, secret_a, _a = await _user_with_token(
        session, slug="mcp00002", email="alice@example.org"
    )
    _tb, _ub, secret_b, _b = await _user_with_token(
        session, slug="mcp00003", email="bob@example.org"
    )
    whoami = await _tool(server, "whoami")

    with _calling_with(secret_a):
        assert json.loads(await whoami())["email"] == "alice@example.org"
    with _calling_with(secret_b):
        assert json.loads(await whoami())["email"] == "bob@example.org"


async def test_listing_shows_only_the_callers_own_datasets(server, session: AsyncSession) -> None:
    """A leak here would hand one user another's dataset names."""
    tenant_a, _ua, secret_a, _a = await _user_with_token(
        session, slug="mcp00004", email="alice2@example.org"
    )
    tenant_b, _ub, secret_b, _b = await _user_with_token(
        session, slug="mcp00005", email="bob2@example.org"
    )
    session.add_all(
        [
            make_dataset(tenant=tenant_a, name="alices-work"),
            make_dataset(tenant=tenant_b, name="bobs-work"),
        ]
    )
    await session.commit()

    list_datasets = await _tool(server, "list_datasets")
    with _calling_with(secret_a):
        assert [d["name"] for d in json.loads(await list_datasets())] == ["alices-work"]
    with _calling_with(secret_b):
        assert [d["name"] for d in json.loads(await list_datasets())] == ["bobs-work"]


async def test_another_users_dataset_is_not_reachable_by_name(
    server, session: AsyncSession
) -> None:
    """Knowing the name must not be enough."""
    _ta, _ua, secret_a, _a = await _user_with_token(
        session, slug="mcp00006", email="alice3@example.org"
    )
    tenant_b, _ub, _secret_b, _b = await _user_with_token(
        session, slug="mcp00007", email="bob3@example.org"
    )
    session.add(make_dataset(tenant=tenant_b, name="bobs-secret"))
    await session.commit()

    get_dataset = await _tool(server, "get_dataset")
    with _calling_with(secret_a), pytest.raises(ValueError, match="No dataset named"):
        await get_dataset("bobs-secret")


async def test_a_write_lands_in_the_callers_own_workspace(server, session: AsyncSession) -> None:
    tenant, _user, secret, _token = await _user_with_token(
        session, slug="mcp00008", email="writer@example.org"
    )

    create = await _tool(server, "create_dataset")
    save = await _tool(server, "save_dataset")
    with _calling_with(secret):
        await create("from-an-agent", "miappe", "1.1")
        await save("from-an-agent", {"entities": [{"type": "Investigation"}]})

    # Read back through the same factory the tools use, not the fixture's
    # session: the write happened in its own session, which is the behaviour
    # under test.
    from sqlalchemy import select

    from metaseed_hub.database import db

    async with db.session_factory() as check:
        stored = (
            await check.execute(select(Dataset).where(Dataset.name == "from-an-agent"))
        ).scalar_one()
        assert stored.tenant_id == tenant.id
        assert stored.data == {"entities": [{"type": "Investigation"}]}


async def test_a_duplicate_name_is_refused(server, session: AsyncSession) -> None:
    tenant, _user, secret, _token = await _user_with_token(
        session, slug="mcp00009", email="dup@example.org"
    )
    session.add(make_dataset(tenant=tenant, name="taken"))
    await session.commit()

    create = await _tool(server, "create_dataset")
    with _calling_with(secret), pytest.raises(ValueError, match="already exists"):
        await create("taken", "miappe", "1.1")


async def test_profiles_include_published_specs(server, session: AsyncSession) -> None:
    """Publishing shares a specification with every user, so an agent can use
    one it did not write."""
    from metaseed_hub.models import SpecStatus
    from tests.factories import make_spec

    tenant_a, user_a, secret_a, _a = await _user_with_token(
        session, slug="mcp00010", email="reader@example.org"
    )
    tenant_b, user_b, _sb, _b = await _user_with_token(
        session, slug="mcp00011", email="author@example.org"
    )
    session.add(
        make_spec(
            tenant=tenant_b,
            created_by=user_b,
            name="SharedSpec",
            status=SpecStatus.PUBLISHED,
        )
    )
    await session.commit()

    list_profiles = await _tool(server, "list_profiles")
    with _calling_with(secret_a):
        result = json.loads(await list_profiles())

    assert "SharedSpec" in [p["name"] for p in result["published"]]
    assert result["built_in"], "the packaged standards are still offered"


class TestOverHTTP:
    """Driving the endpoint the way a client does.

    The tool-level tests above call the functions directly, so they passed while
    the endpoint itself was unreachable: it was mounted at /hub/mcp but FastMCP
    serves at its own ``streamable_http_path``, putting the real path at
    /hub/mcp/mcp, and a mounted sub-app's lifespan never ran so the session
    manager was uninitialised. Both produced a broken endpoint under a green
    suite.
    """

    @staticmethod
    def _initialize() -> dict:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }

    def test_the_endpoint_answers_at_hub_mcp(self) -> None:
        """Not /hub/mcp/mcp, and not a 500 from an unstarted session manager."""
        from fastapi.testclient import TestClient

        from metaseed_hub.main import create_app

        with TestClient(create_app()) as client:
            response = client.post(
                "/hub/mcp",
                json=self._initialize(),
                headers={"Accept": "application/json, text/event-stream"},
            )

        assert response.status_code != 404, "the endpoint is not where it is mounted"
        assert response.status_code < 500, f"server error: {response.text[:300]}"

    def test_the_doubled_path_is_not_the_endpoint(self) -> None:
        """Guards the regression directly: /hub/mcp/mcp must not be where it lives."""
        from fastapi.testclient import TestClient

        from metaseed_hub.main import create_app

        with TestClient(create_app()) as client:
            response = client.post(
                "/hub/mcp/mcp",
                json=self._initialize(),
                headers={"Accept": "application/json, text/event-stream"},
            )

        assert response.status_code == 404


class TestHardening:
    """What stops an agent doing irreversible damage to someone's work.

    A tool call is not a person clicking a button: an agent can replace a whole
    dataset in one step, and can do it in a loop. These verify the write path
    leaves a way back and refuses obvious runaways.
    """

    async def test_an_overwrite_keeps_the_previous_contents(
        self, server, session: AsyncSession
    ) -> None:
        """The most dangerous thing here: save_dataset replaces everything."""
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import DatasetVersion

        tenant, _user, secret, _token = await _user_with_token(
            session, slug="hard0001", email="h1@example.org"
        )
        original = {"entities": [{"type": "Investigation", "title": "keep me"}]}
        session.add(make_dataset(tenant=tenant, name="overwritten", data=original))
        await session.commit()

        save = await _tool(server, "save_dataset")
        with _calling_with(secret):
            await save("overwritten", {"entities": []})

        async with db.session_factory() as check:
            versions = (await check.execute(select(DatasetVersion))).scalars().all()
        assert len(versions) == 1, "the previous contents must be recoverable"
        assert versions[0].data == original

    async def test_an_unchanged_save_does_not_pile_up_versions(
        self, server, session: AsyncSession
    ) -> None:
        """An agent that writes the same thing repeatedly must not bury the
        history it exists to protect."""
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import DatasetVersion

        tenant, _user, secret, _token = await _user_with_token(
            session, slug="hard0002", email="h2@example.org"
        )
        data = {"entities": [{"type": "Investigation"}]}
        session.add(make_dataset(tenant=tenant, name="idempotent", data=data))
        await session.commit()

        save = await _tool(server, "save_dataset")
        with _calling_with(secret):
            await save("idempotent", data)
            await save("idempotent", data)

        async with db.session_factory() as check:
            versions = (await check.execute(select(DatasetVersion))).scalars().all()
        assert versions == []

    async def test_an_oversized_dataset_is_refused(self, server, session: AsyncSession) -> None:
        """A runaway loop should be stopped, not stored."""
        from metaseed_hub.mcp import MAX_DATASET_BYTES

        tenant, _user, secret, _token = await _user_with_token(
            session, slug="hard0003", email="h3@example.org"
        )
        session.add(make_dataset(tenant=tenant, name="big", data={}))
        await session.commit()

        save = await _tool(server, "save_dataset")
        huge = {"blob": "x" * (MAX_DATASET_BYTES + 1000)}
        with _calling_with(secret), pytest.raises(ValueError, match="the limit is"):
            await save("big", huge)

    async def test_deleting_is_soft(self, server, session: AsyncSession) -> None:
        """An agent must not be able to destroy work irrecoverably."""
        from metaseed_hub.database import db

        tenant, _user, secret, _token = await _user_with_token(
            session, slug="hard0004", email="h4@example.org"
        )
        dataset = make_dataset(tenant=tenant, name="removable")
        session.add(dataset)
        await session.commit()
        dataset_id = dataset.id

        delete = await _tool(server, "delete_dataset")
        with _calling_with(secret):
            await delete("removable")

        async with db.session_factory() as check:
            stored = await check.get(Dataset, dataset_id)
            assert stored is not None, "soft, not erased"
            assert stored.deleted_at is not None

    async def test_another_users_dataset_cannot_be_deleted(
        self, server, session: AsyncSession
    ) -> None:
        _ta, _ua, secret_a, _a = await _user_with_token(
            session, slug="hard0005", email="h5@example.org"
        )
        tenant_b, _ub, _sb, _b = await _user_with_token(
            session, slug="hard0006", email="h6@example.org"
        )
        session.add(make_dataset(tenant=tenant_b, name="not-yours"))
        await session.commit()

        delete = await _tool(server, "delete_dataset")
        with _calling_with(secret_a), pytest.raises(ValueError, match="No dataset named"):
            await delete("not-yours")


def _one_empty_investigation() -> dict:
    """A dataset in the shape the hub stores, holding one incomplete entity.

    Built through metaseed rather than hand-written, so the test cannot drift
    from the serialization the facade actually loads.
    """
    from metaseed import MetaseedClient

    client = MetaseedClient(profile="miappe", version="1.1")
    client.create_entity("Investigation", {}, skip_validation=True)
    return client.serialize()


class TestFeedback:
    """Saving reports what is still wrong, using metaseed's own validation.

    The issues come from the spec definition — which field is required, which
    relationship needs a minimum — so they are passed through rather than
    re-derived here; re-deriving would only let the two disagree.
    """

    async def test_saving_reports_what_is_still_missing(
        self, server, session: AsyncSession
    ) -> None:
        """A bare "saved: true" lets an agent believe it has finished."""
        tenant, _user, secret, _token = await _user_with_token(
            session, slug="fb000001", email="fb1@example.org"
        )
        session.add(make_dataset(tenant=tenant, name="incomplete", profile="miappe"))
        await session.commit()

        save = await _tool(server, "save_dataset")
        with _calling_with(secret):
            result = json.loads(await save("incomplete", _one_empty_investigation()))

        assert result["saved"] is True
        assert result["valid"] is False, "an empty Investigation is not complete"
        assert result["issues"], "the agent must be told what is missing"
        assert "next_step" in result

    async def test_the_issues_carry_the_spec_rule_that_failed(
        self, server, session: AsyncSession
    ) -> None:
        """`rule` names which spec rule failed and is the actionable part;
        dropping it leaves only prose to parse."""
        tenant, _user, secret, _token = await _user_with_token(
            session, slug="fb000002", email="fb2@example.org"
        )
        session.add(make_dataset(tenant=tenant, name="ruled", profile="miappe"))
        await session.commit()

        save = await _tool(server, "save_dataset")
        with _calling_with(secret):
            result = json.loads(await save("ruled", _one_empty_investigation()))

        assert all("rule" in i and i["rule"] for i in result["issues"])
        assert all("field" in i for i in result["issues"])
        assert "required_fields" in {i["rule"] for i in result["issues"]}

    async def test_the_schema_marks_required_fields(self, server, session: AsyncSession) -> None:
        """An agent cannot fill required fields it was never shown."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="fb000003", email="fb3@example.org"
        )

        schema = await _tool(server, "get_profile_schema")
        with _calling_with(secret):
            result = json.loads(await schema("miappe", "1.1"))

        fields = result["entities"]["Investigation"]["fields"]
        assert any(f["required"] for f in fields), "required fields must be flagged"
        assert fields[0]["required"], "required fields come first"
        assert "guidance" in result


class TestEntityTools:
    """Editing one entity without rewriting the dataset.

    With only save_dataset an agent had to resend everything to change a field,
    which overwrites whatever else changed meanwhile. These assert the entity
    actually lands in the stored dataset, not merely that the call returned.
    """

    async def _dataset(self, session: AsyncSession, *, slug: str, name: str):
        tenant, _user, secret, _token = await _user_with_token(
            session, slug=slug, email=f"{slug}@example.org"
        )
        session.add(make_dataset(tenant=tenant, name=name, profile="miappe", version="1.1"))
        await session.commit()
        return tenant, secret

    async def _stored(self, name: str) -> dict:
        from sqlalchemy import select

        from metaseed_hub.database import db

        async with db.session_factory() as check:
            row = (await check.execute(select(Dataset).where(Dataset.name == name))).scalar_one()
            return row.data or {}

    async def test_creating_an_entity_persists_it(self, server, session: AsyncSession) -> None:
        _t, secret = await self._dataset(session, slug="ent00001", name="ds1")

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            result = json.loads(await create("ds1", "Investigation", {"title": "My study"}))

        assert result["entity_type"] == "Investigation"
        entities = (await self._stored("ds1"))["entities"]
        assert [e["_type"] for e in entities] == ["Investigation"]
        assert entities[0]["title"] == "My study"

    async def test_creating_an_entity_reports_what_is_still_missing(
        self, server, session: AsyncSession
    ) -> None:
        """An incomplete entity is accepted, but the agent is told."""
        _t, secret = await self._dataset(session, slug="ent00002", name="ds2")

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            result = json.loads(await create("ds2", "Investigation", {}))

        assert result["valid"] is False
        assert any(i["rule"] == "required_fields" for i in result["issues"])

    async def test_updating_leaves_other_fields_alone(self, server, session: AsyncSession) -> None:
        """The whole point of entity-level editing."""
        _t, secret = await self._dataset(session, slug="ent00003", name="ds3")

        create = await _tool(server, "create_entity")
        update = await _tool(server, "update_entity")
        with _calling_with(secret):
            created = json.loads(
                await create("ds3", "Investigation", {"title": "Keep", "unique_id": "U1"})
            )
            await update("ds3", created["id"], {"unique_id": "U2"})

        entity = (await self._stored("ds3"))["entities"][0]
        assert entity["unique_id"] == "U2", "the named field changed"
        assert entity["title"] == "Keep", "the unnamed field survived"

    async def test_deleting_removes_only_that_entity(self, server, session: AsyncSession) -> None:
        _t, secret = await self._dataset(session, slug="ent00004", name="ds4")

        create = await _tool(server, "create_entity")
        delete = await _tool(server, "delete_entity")
        with _calling_with(secret):
            a = json.loads(await create("ds4", "Investigation", {"title": "A"}))
            await create("ds4", "Investigation", {"title": "B"})
            await delete("ds4", a["id"])

        titles = [e["title"] for e in (await self._stored("ds4"))["entities"]]
        assert titles == ["B"]

    async def test_an_edit_snapshots_the_previous_state(
        self, server, session: AsyncSession
    ) -> None:
        """Entity edits are many small writes; each must stay recoverable."""
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import DatasetVersion

        _t, secret = await self._dataset(session, slug="ent00005", name="ds5")

        create = await _tool(server, "create_entity")
        with _calling_with(secret):
            await create("ds5", "Investigation", {"title": "One"})
            await create("ds5", "Investigation", {"title": "Two"})

        async with db.session_factory() as check:
            versions = (await check.execute(select(DatasetVersion))).scalars().all()
        assert len(versions) == 2, "each edit leaves a way back"

    async def test_entities_can_be_listed_and_read(self, server, session: AsyncSession) -> None:
        _t, secret = await self._dataset(session, slug="ent00006", name="ds6")

        create = await _tool(server, "create_entity")
        listing = await _tool(server, "list_entities")
        get = await _tool(server, "get_entity")
        with _calling_with(secret):
            made = json.loads(await create("ds6", "Investigation", {"title": "Findable"}))
            listed = json.loads(await listing("ds6"))
            one = json.loads(await get("ds6", made["id"]))

        assert [e["entity_type"] for e in listed["entities"]] == ["Investigation"]
        assert one["data"]["title"] == "Findable"

    async def test_another_users_dataset_cannot_be_edited(
        self, server, session: AsyncSession
    ) -> None:
        _ta, secret_a = await self._dataset(session, slug="ent00007", name="mine")
        tenant_b, _ub, _sb, _b = await _user_with_token(
            session, slug="ent00008", email="other@example.org"
        )
        session.add(make_dataset(tenant=tenant_b, name="theirs", profile="miappe"))
        await session.commit()

        create = await _tool(server, "create_entity")
        with _calling_with(secret_a), pytest.raises(ValueError, match="No dataset named"):
            await create("theirs", "Investigation", {"title": "nope"})


class TestSpecTools:
    """Building a specification through the agent.

    The logic is metaseed's ``SpecBuilder``, already shared with the web UI; the
    hub adds loading and saving. So these assert the draft in the database
    actually changed, which is the only part the hub is responsible for.
    """

    async def _drafting(self, server, session: AsyncSession, *, slug: str, name: str):
        _t, _u, secret, _token = await _user_with_token(
            session, slug=slug, email=f"{slug}@example.org"
        )
        create = await _tool(server, "spec_create")
        with _calling_with(secret):
            await create(name, "1.0", "a test specification")
        return secret

    async def _spec_of(self, name: str) -> dict:
        from sqlalchemy import select

        from metaseed_hub.database import db
        from metaseed_hub.models import SpecDraft

        async with db.session_factory() as check:
            draft = (
                await check.execute(select(SpecDraft).where(SpecDraft.name == name))
            ).scalar_one()
            return draft.spec_data["spec"]

    async def test_a_draft_is_created(self, server, session: AsyncSession) -> None:
        await self._drafting(server, session, slug="spec0001", name="MyProfile")

        spec = await self._spec_of("MyProfile")
        assert spec["name"] == "MyProfile"
        assert spec["version"] == "1.0"

    async def test_entities_and_fields_are_added(self, server, session: AsyncSession) -> None:
        secret = await self._drafting(server, session, slug="spec0002", name="Built")

        add_entity = await _tool(server, "spec_add_entity")
        add_field = await _tool(server, "spec_add_field")
        with _calling_with(secret):
            await add_entity("Built", "Study", "One study")
            await add_field("Built", "Study", "title", "string", required=True)

        spec = await self._spec_of("Built")
        assert "Study" in spec["entities"]
        fields = spec["entities"]["Study"]["fields"]
        assert [f["name"] for f in fields] == ["title"]
        assert fields[0]["required"] is True

    async def test_the_root_entity_can_be_set(self, server, session: AsyncSession) -> None:
        secret = await self._drafting(server, session, slug="spec0003", name="Rooted")

        add_entity = await _tool(server, "spec_add_entity")
        set_root = await _tool(server, "spec_set_root_entity")
        with _calling_with(secret):
            await add_entity("Rooted", "Investigation")
            await set_root("Rooted", "Investigation")

        assert (await self._spec_of("Rooted"))["root_entity"] == "Investigation"

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

        secret = await self._drafting(server, session, slug="spec0004", name="Empty")

        validate = await _tool(server, "spec_validate")
        with _calling_with(secret):
            result = json.loads(await validate("Empty"))

        assert result["problems"] == SpecBuilder.empty("Empty", "1.0").validate()
        assert result["valid"] is True

    async def test_a_bad_edit_is_refused_by_metaseed(self, server, session: AsyncSession) -> None:
        """Rejections come from SpecBuilder too, and reach the agent as errors
        rather than being silently swallowed."""
        secret = await self._drafting(server, session, slug="spec0008", name="Strict")

        set_root = await _tool(server, "spec_set_root_entity")
        with _calling_with(secret), pytest.raises(ValueError, match="not found"):
            await set_root("Strict", "NoSuchEntity")

    async def test_yaml_can_be_previewed(self, server, session: AsyncSession) -> None:
        secret = await self._drafting(server, session, slug="spec0005", name="Yamly")

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
        secret_a = await self._drafting(server, session, slug="spec0006", name="Mine")
        await self._drafting(server, session, slug="spec0007", name="Theirs")

        add_entity = await _tool(server, "spec_add_entity")
        with _calling_with(secret_a), pytest.raises(ValueError, match="No specification draft"):
            await add_entity("Theirs", "Sneaky")


async def test_the_instructions_cover_the_tools_that_exist(server) -> None:
    """The instructions described only the original nine tools after entity and
    spec tools were added, so an agent had no reason to think it could edit
    incrementally — and would keep replacing whole datasets."""
    instructions = server.instructions or ""

    for expected in ("create_entity", "update_entity", "spec_create", "spec_validate"):
        assert expected in instructions, f"{expected} is not mentioned"
    assert "save_dataset, which replaces the whole dataset" in instructions
    assert "Publishing is deliberately not available" in instructions
