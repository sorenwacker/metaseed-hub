"""Datasets record which specification they were written against.

``dataset.version`` does not answer the question. A specification can be edited
without its version changing, and two releases can declare the same version and
differ, so a dataset that validated when it was saved can start failing with no
visible cause. The stored envelope therefore carries the specification's content
hash, and loading compares it with the specification's current hash.

The comparison is *reported*, never enforced: a drifted dataset still opens,
still edits, and still validates. And every dataset in production predates the
stamp, so the missing-stamp path is the one that has to be right first -- a
missing stamp means unknown provenance, not unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from metaseed.specs import content_hash
from metaseed.specs.schema import EntityDefSpec, FieldSpec, FieldType, ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset, SpecDraft
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_dataset, make_spec_draft, make_tenant, make_user
from tests.test_mcp_endpoint import _calling_with, _tool, _user_with_token

# Imported under an alias and re-exposed by assignment: pytest discovers the
# fixture by module attribute name, and a plain import would trip F811 on every
# test whose `server` parameter shadows it.
from tests.test_mcp_endpoint import server as mcp_server

server = mcp_server

_UNRELATED_HASH = "sha256:" + "0" * 64


def _tiny_spec(*, extra_field: bool = False) -> ProfileSpec:
    """A one-entity profile, optionally with a field added to change its hash."""
    fields = [FieldSpec(name="id", type=FieldType.STRING, required=True)]
    if extra_field:
        fields.append(FieldSpec(name="notes", type=FieldType.STRING, required=False))
    return ProfileSpec(
        name="tiny",
        version="1.0",
        root_entity="Sample",
        entities={"Sample": EntityDefSpec(description="a sample", fields=fields)},
        validation_rules=[],
    )


def _stored(spec: ProfileSpec) -> dict[str, Any]:
    state = SpecBuilderState()
    state.spec = spec
    return state.to_dict()


async def _dataset_on_a_draft_spec(
    session: AsyncSession, slug: str, *, data: dict[str, Any] | None = None
) -> tuple[Dataset, SpecDraft]:
    """A dataset bound to an editable draft specification, so the spec can change."""
    tenant = make_tenant(slug=slug)
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, email=f"{slug}@example.org")
    session.add(user)
    await session.flush()
    draft = make_spec_draft(
        tenant=tenant, user=user, name="tiny", version="1.0", spec_data=_stored(_tiny_spec())
    )
    session.add(draft)
    await session.flush()
    dataset = make_dataset(tenant=tenant, profile="tiny", version="1.0", data=data or {})
    dataset.spec_draft_id = draft.id
    session.add(dataset)
    await session.commit()
    return dataset, draft


class TestStamping:
    """Every write path records the hash; nothing else has to remember to."""

    async def test_a_dataset_saved_over_mcp_carries_the_stamp(self, server, session) -> None:
        from metaseed_hub.database import db

        _t, _u, secret, _token = await _user_with_token(
            session, slug="stamp-mcp", email="stamp-mcp@example.org"
        )
        create_dataset = await _tool(server, "create_dataset")
        create_entity = await _tool(server, "create_entity")
        with _calling_with(secret):
            await create_dataset("Stamped", "miappe", "1.1")
            await create_entity("Stamped", "Investigation", {"identifier": "INV-1"})

        async with db.session_factory() as check:
            from sqlalchemy import select

            dataset = (
                await check.execute(select(Dataset).where(Dataset.name == "Stamped"))
            ).scalar_one()

        from metaseed.specs.loader import SpecLoader

        assert dataset.data["spec_hash"] == content_hash(SpecLoader().load_profile("1.1", "miappe"))

    async def test_the_web_save_path_carries_the_stamp(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.helpers import ensure_dataset_facade, save_dataset_state

        dataset, _draft = await _dataset_on_a_draft_spec(session, "stamp-web")
        state = await ensure_dataset_facade(dataset, session)

        await save_dataset_state(session, dataset, state)

        assert dataset.data["spec_hash"] == content_hash(_tiny_spec())


class TestDriftReporting:
    """A changed specification is reported, not enforced and not swallowed."""

    async def test_a_changed_specification_is_reported(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.helpers.spec_hash import spec_drift_message

        dataset, draft = await _dataset_on_a_draft_spec(
            session, "drift-real", data={"spec_hash": content_hash(_tiny_spec()), "tree": []}
        )
        draft.spec_data = _stored(_tiny_spec(extra_field=True))
        await session.commit()

        message = await spec_drift_message(session, dataset)

        assert message is not None
        assert "tiny" in message

    async def test_an_unchanged_specification_is_not_reported(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.helpers.spec_hash import spec_drift_message

        dataset, _draft = await _dataset_on_a_draft_spec(
            session, "drift-none", data={"spec_hash": content_hash(_tiny_spec()), "tree": []}
        )

        assert await spec_drift_message(session, dataset) is None

    async def test_a_dataset_with_no_stamp_reports_no_drift(self, session: AsyncSession) -> None:
        """Every dataset in production is in this state. Unknown, not unchanged."""
        from metaseed_hub.ui.helpers.spec_hash import spec_drift_message

        dataset, draft = await _dataset_on_a_draft_spec(session, "drift-unstamped")
        draft.spec_data = _stored(_tiny_spec(extra_field=True))
        await session.commit()

        assert await spec_drift_message(session, dataset) is None

    async def test_an_unresolvable_specification_reports_no_drift(
        self, session: AsyncSession
    ) -> None:
        """Nothing to compare against is not evidence of a change."""
        from metaseed_hub.ui.helpers.spec_hash import spec_drift_message

        tenant = make_tenant(slug="drift-noprofile")
        session.add(tenant)
        await session.flush()
        dataset = make_dataset(
            tenant=tenant,
            profile="does-not-exist",
            version="9.9",
            data={"spec_hash": _UNRELATED_HASH, "tree": []},
        )
        session.add(dataset)
        await session.commit()

        assert await spec_drift_message(session, dataset) is None


class TestTheReportingSurfaces:
    """Drift reaches the user through the paths that already report problems."""

    async def test_the_mcp_validation_report_carries_it(self, server, session) -> None:
        from metaseed_hub.database import db

        _t, _u, secret, _token = await _user_with_token(
            session, slug="drift-mcp", email="drift-mcp@example.org"
        )
        create_dataset = await _tool(server, "create_dataset")
        validate = await _tool(server, "validate_dataset")
        with _calling_with(secret):
            await create_dataset("Drifted", "miappe", "1.1")

        async with db.session_factory() as write:
            from sqlalchemy import select
            from sqlalchemy.orm.attributes import flag_modified

            dataset = (
                await write.execute(select(Dataset).where(Dataset.name == "Drifted"))
            ).scalar_one()
            dataset.data = {**dataset.data, "spec_hash": _UNRELATED_HASH}
            flag_modified(dataset, "data")
            await write.commit()

        with _calling_with(secret):
            report = json.loads(await validate("Drifted"))

        assert any(issue["rule"] == "spec_drift" for issue in report["issues"]), report

    async def test_drift_does_not_change_whether_the_dataset_is_valid(
        self, server, session
    ) -> None:
        """It is provenance, not a validation failure. Reported, never enforced."""
        from metaseed_hub.database import db

        _t, _u, secret, _token = await _user_with_token(
            session, slug="drift-valid", email="drift-valid@example.org"
        )
        create_dataset = await _tool(server, "create_dataset")
        validate = await _tool(server, "validate_dataset")
        with _calling_with(secret):
            await create_dataset("StillValid", "miappe", "1.1")
            clean = json.loads(await validate("StillValid"))

        async with db.session_factory() as write:
            from sqlalchemy import select
            from sqlalchemy.orm.attributes import flag_modified

            dataset = (
                await write.execute(select(Dataset).where(Dataset.name == "StillValid"))
            ).scalar_one()
            dataset.data = {**dataset.data, "spec_hash": _UNRELATED_HASH}
            flag_modified(dataset, "data")
            await write.commit()

        with _calling_with(secret):
            drifted = json.loads(await validate("StillValid"))

        assert drifted["valid"] == clean["valid"]

    async def test_the_web_validation_panel_carries_it(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.helpers import ensure_dataset_facade
        from metaseed_hub.ui.routes.dataset.editor import _render_validation_results

        dataset, _draft = await _dataset_on_a_draft_spec(
            session, "drift-web", data={"spec_hash": _UNRELATED_HASH, "tree": []}
        )
        state = await ensure_dataset_facade(dataset, session)

        html = _render_validation_results(dataset.id, state, [], drift="tiny has changed")

        assert "tiny has changed" in html
