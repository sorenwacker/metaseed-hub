"""The dataset-level MCP tools: listing, reading, writing and deleting.

These cover what one caller may reach -- only their own datasets, by name --
and what the write path guarantees once they do: a recoverable previous
version, a refusal for a runaway payload, a soft delete, and validation
feedback telling the agent what is still missing.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset
from tests.factories import make_dataset
from tests.mcp_helpers import _calling_with, _tool, _user_with_token


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


async def test_a_write_lands_in_the_callers_own_account(server, session: AsyncSession) -> None:
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


class TestCreateDatasetNames:
    """create_dataset must fail with an error an agent can act on.

    The unique constraint on (tenant, name) is not scoped to deleted_at, so a
    soft-deleted row still holds its name and a blind insert dies with an
    IntegrityError the agent cannot interpret.
    """

    async def test_a_soft_deleted_name_is_refused_with_a_reason(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="name0001", email="n1@example.org"
        )

        create = await _tool(server, "create_dataset")
        delete = await _tool(server, "delete_dataset")
        with _calling_with(secret):
            await create("recycled", "miappe", "1.1")
            await delete("recycled")
            with pytest.raises(ValueError, match="deleted dataset"):
                await create("recycled", "miappe", "1.1")

    async def test_an_unknown_profile_is_refused(self, server, session: AsyncSession) -> None:
        """A dataset bound to a profile nothing can load is unusable."""
        _t, _u, secret, _token = await _user_with_token(
            session, slug="name0002", email="n2@example.org"
        )

        create = await _tool(server, "create_dataset")
        with _calling_with(secret), pytest.raises(ValueError, match="list_profiles"):
            await create("doomed", "no-such-profile", "1.0")

    async def test_an_unknown_version_is_refused(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="name0003", email="n3@example.org"
        )

        create = await _tool(server, "create_dataset")
        with _calling_with(secret), pytest.raises(ValueError, match="no version"):
            await create("doomed", "miappe", "9.9")
