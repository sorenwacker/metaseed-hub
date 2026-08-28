"""Spec-draft roles must actually gate what a member may do.

Members are stored with a viewer/editor/owner role, but the mutating routes
used to check only membership, so a member explicitly shared as VIEWER could
edit entities, reset the draft to empty, and even publish it. These tests pin
the enforcement: role resolution in ``get_draft_role``, the write gate in
``get_draft_context``, and the owner-only publish/reset routes. They also pin
that a fork of someone else's published spec is publishable by its forker,
which the old source-spec permission check wrongly rejected.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import EntityDefSpec, ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import (
    Dataset,
    Role,
    Spec,
    SpecDraft,
    SpecDraftMember,
    Tenant,
    User,
)
from metaseed_hub.ui.spec_builder import access
from metaseed_hub.ui.spec_builder.access import (
    get_draft_context,
    get_draft_role,
    require_edit_role,
    require_owner_role,
)
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_dataset, make_spec, make_spec_draft, make_tenant, make_user


def _spec() -> ProfileSpec:
    return ProfileSpec(
        name="demo",
        version="0.1",
        root_entity="Investigation",
        entities={"Investigation": EntityDefSpec(description="root", fields=[])},
    )


def _request(method: str) -> Request:
    return Request({"type": "http", "method": method, "path": "/", "headers": []})


def _draft_endpoint(path_suffix: str, method: str) -> Any:
    """The registered endpoint function for a draft route."""
    router = APIRouter()
    register_draft_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    for route in router.routes:
        if route.path.endswith(path_suffix) and method in route.methods:  # type: ignore[attr-defined]
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError(f"no route {method} ...{path_suffix}")


async def _shared_draft(
    session: AsyncSession,
) -> tuple[SpecDraft, Tenant, User, dict[Role, User]]:
    """A draft with one member per role, all in the owner's tenant."""
    tenant = make_tenant()
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant, email="owner@example.org")
    session.add(owner)
    await session.flush()
    draft = make_spec_draft(
        tenant=tenant,
        user=owner,
        name="demo",
        spec_data=SpecBuilderState(spec=_spec()).to_dict(),
    )
    session.add(draft)
    await session.flush()
    members: dict[Role, User] = {}
    for role in Role:
        user = make_user(tenant=tenant, email=f"member-{role.value}@example.org")
        session.add(user)
        await session.flush()
        session.add(SpecDraftMember(spec_draft_id=draft.id, user_id=user.id, role=role))
        members[role] = user
    await session.commit()
    return draft, tenant, owner, members


class TestGetDraftRole:
    """Role resolution across the three access-grant paths."""

    async def test_draft_owner_holds_owner_role(self, session: AsyncSession) -> None:
        draft, _tenant, owner, _members = await _shared_draft(session)
        assert await get_draft_role(session, draft, owner.id) is Role.OWNER

    async def test_members_hold_their_recorded_role(self, session: AsyncSession) -> None:
        draft, _tenant, _owner, members = await _shared_draft(session)
        for role, user in members.items():
            assert await get_draft_role(session, draft, user.id) is role

    async def test_a_second_user_in_the_account_holds_no_role(self, session: AsyncSession) -> None:
        """There is no tenant-wide grant: an account belongs to one person, so a
        second user in it (impossible in production) is a stranger to the draft."""
        draft, tenant, _owner, _members = await _shared_draft(session)
        colleague = make_user(tenant=tenant, email="colleague@example.org")
        session.add(colleague)
        await session.commit()
        assert await get_draft_role(session, draft, colleague.id) is None

    async def test_outsider_holds_no_role(self, session: AsyncSession) -> None:
        draft, _tenant, _owner, _members = await _shared_draft(session)
        other_tenant = make_tenant()
        session.add(other_tenant)
        await session.flush()
        outsider = make_user(tenant=other_tenant, email="outsider@example.org")
        session.add(outsider)
        await session.commit()
        assert await get_draft_role(session, draft, outsider.id) is None


class TestWriteGate:
    """Viewers must not pass the mutation choke points."""

    async def test_viewer_is_refused_edit_access(self, session: AsyncSession) -> None:
        draft, _tenant, _owner, members = await _shared_draft(session)
        viewer = members[Role.VIEWER]
        with pytest.raises(HTTPException) as err:
            await require_edit_role(session, draft, viewer.id)
        assert err.value.status_code == 403

    async def test_editor_passes_edit_but_not_owner_gate(self, session: AsyncSession) -> None:
        draft, _tenant, _owner, members = await _shared_draft(session)
        editor = members[Role.EDITOR]
        await require_edit_role(session, draft, editor.id)
        with pytest.raises(HTTPException) as err:
            await require_owner_role(session, draft, editor.id)
        assert err.value.status_code == 403

    async def test_owner_member_passes_owner_gate(self, session: AsyncSession) -> None:
        draft, _tenant, _owner, members = await _shared_draft(session)
        await require_owner_role(session, draft, members[Role.OWNER].id)

    async def test_a_viewer_cannot_send_mutating_requests(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_draft_context, the dependency of every entity/field/rule route,
        must reject non-GET requests from a viewer."""
        draft, tenant, _owner, members = await _shared_draft(session)
        viewer = members[Role.VIEWER]

        async def _as_viewer(
            request: Request, sess: AsyncSession, **kwargs: Any
        ) -> tuple[str, str]:
            return viewer.id, tenant.id

        monkeypatch.setattr(access, "get_user_context", _as_viewer)

        ctx = await get_draft_context(_request("GET"), draft.id, session)
        assert ctx.user_id == viewer.id, "reading must keep working for viewers"

        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with pytest.raises(HTTPException) as err:
                await get_draft_context(_request(method), draft.id, session)
            assert err.value.status_code == 403, f"{method} must be refused for a viewer"

    async def test_an_editor_can_send_mutating_requests(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        draft, tenant, _owner, members = await _shared_draft(session)
        editor = members[Role.EDITOR]

        async def _as_editor(
            request: Request, sess: AsyncSession, **kwargs: Any
        ) -> tuple[str, str]:
            return editor.id, tenant.id

        monkeypatch.setattr(access, "get_user_context", _as_editor)

        ctx = await get_draft_context(_request("POST"), draft.id, session)
        assert ctx.user_id == editor.id


class TestOwnerOnlyRoutes:
    """Reset and publish are destructive and reserved for the OWNER role."""

    async def test_an_editor_cannot_reset_the_draft(self, session: AsyncSession) -> None:
        draft, tenant, _owner, members = await _shared_draft(session)
        endpoint = _draft_endpoint("/reset", "POST")
        with pytest.raises(HTTPException) as err:
            await endpoint(
                request=_request("POST"),
                draft_id=draft.id,
                session=session,
                user_ctx=(members[Role.EDITOR].id, tenant.id),
            )
        assert err.value.status_code == 403
        await session.refresh(draft)
        assert draft.spec_data["spec"]["entities"], "the draft must not have been emptied"

    async def test_the_owner_can_reset_the_draft(self, session: AsyncSession) -> None:
        draft, tenant, owner, _members = await _shared_draft(session)
        endpoint = _draft_endpoint("/reset", "POST")
        response = await endpoint(
            request=_request("POST"),
            draft_id=draft.id,
            session=session,
            user_ctx=(owner.id, tenant.id),
        )
        assert response.status_code == 303
        await session.refresh(draft)
        assert draft.spec_data["spec"]["entities"] == {}

    async def test_a_viewer_cannot_publish_the_draft(self, session: AsyncSession) -> None:
        draft, tenant, _owner, members = await _shared_draft(session)
        endpoint = _draft_endpoint("/publish", "POST")
        with pytest.raises(HTTPException) as err:
            await endpoint(
                request=_request("POST"),
                draft_id=draft.id,
                session=session,
                user_ctx=(members[Role.VIEWER].id, tenant.id),
            )
        assert err.value.status_code == 403
        drafts = (await session.execute(select(SpecDraft))).scalars().all()
        assert drafts, "the draft must survive a refused publish"

    async def test_an_owner_role_member_can_publish(self, session: AsyncSession) -> None:
        draft, tenant, _owner, members = await _shared_draft(session)
        endpoint = _draft_endpoint("/publish", "POST")
        await endpoint(
            request=_request("POST"),
            draft_id=draft.id,
            session=session,
            user_ctx=(members[Role.OWNER].id, tenant.id),
        )
        specs = (await session.execute(select(Spec))).scalars().all()
        assert len(specs) == 1
        assert specs[0].name == "demo"


class TestForkPublishing:
    """Anyone may fork a published spec, so a fork must be publishable."""

    async def test_a_forker_can_publish_their_fork(self, session: AsyncSession) -> None:
        """The regression: publish checked edit rights on the fork's source
        spec, so a forker who was not its author could never publish."""
        author_tenant = make_tenant()
        session.add(author_tenant)
        await session.flush()
        author = make_user(tenant=author_tenant, email="author@example.org")
        session.add(author)
        await session.flush()
        source = make_spec(
            tenant=author_tenant,
            created_by=author,
            name="source",
            spec_data=SpecBuilderState(spec=_spec()).to_dict(),
        )
        session.add(source)
        await session.flush()

        forker_tenant = make_tenant()
        session.add(forker_tenant)
        await session.flush()
        forker = make_user(tenant=forker_tenant, email="forker@example.org")
        session.add(forker)
        await session.flush()
        fork = make_spec_draft(
            tenant=forker_tenant,
            user=forker,
            name="demo",
            spec_data=SpecBuilderState(spec=_spec()).to_dict(),
            source_spec=source,
        )
        session.add(fork)
        await session.commit()

        endpoint = _draft_endpoint("/publish", "POST")
        await endpoint(
            request=_request("POST"),
            draft_id=fork.id,
            session=session,
            user_ctx=(forker.id, forker_tenant.id),
        )

        published = (
            (await session.execute(select(Spec).where(Spec.tenant_id == forker_tenant.id)))
            .scalars()
            .all()
        )
        assert len(published) == 1, "the fork must publish into the forker's own tenant"
        await session.refresh(source)
        assert source.deleted_at is None, "publishing a fork must not touch the source spec"


class TestPublishKeepsDatasetsBound:
    """Publishing deleted the draft, and `Dataset.spec_draft_id` is SET NULL —
    so every dataset built on the draft lost its specification the moment the
    spec was released, and its editor was disabled with 'no specification is
    recorded for it'. `delete_draft` refuses in exactly this situation; publish
    performed the same deletion unchecked. Publish is the moment the draft
    *becomes* the spec, so the datasets are rebound to the new Spec row."""

    async def test_a_dataset_on_the_draft_is_rebound_to_the_published_spec(
        self, session: AsyncSession
    ) -> None:
        draft, tenant, owner, _members = await _shared_draft(session)
        dataset = make_dataset(tenant=tenant, name="built-on-draft")
        dataset.spec_draft_id = draft.id
        session.add(dataset)
        await session.commit()
        dataset_id = dataset.id

        endpoint = _draft_endpoint("/publish", "POST")
        await endpoint(
            request=_request("POST"),
            draft_id=draft.id,
            session=session,
            user_ctx=(owner.id, tenant.id),
        )

        spec = (await session.execute(select(Spec))).scalars().one()
        bound = await session.get(Dataset, dataset_id)
        assert bound is not None
        assert bound.spec_id == spec.id, "the dataset follows the draft into release"

    async def test_publishing_the_same_content_twice_reports_not_500s(
        self, session: AsyncSession
    ) -> None:
        """The (tenant, name, version) unique index turned a double publish
        into an unhandled IntegrityError."""
        draft, tenant, owner, _members = await _shared_draft(session)
        endpoint = _draft_endpoint("/publish", "POST")
        await endpoint(
            request=_request("POST"),
            draft_id=draft.id,
            session=session,
            user_ctx=(owner.id, tenant.id),
        )

        # A second draft in the same tenant, same name and version.
        draft2 = make_spec_draft(
            tenant=tenant,
            user=owner,
            name="demo-second",
            spec_data=SpecBuilderState(spec=_spec()).to_dict(),
        )
        session.add(draft2)
        await session.commit()
        response = await endpoint(
            request=_request("POST"),
            draft_id=draft2.id,
            session=session,
            user_ctx=(owner.id, tenant.id),
        )

        body = response.body.decode()
        assert "already" in body.lower() or "exists" in body.lower()


class TestMetadataVersionIsValidated:
    """`ctx.spec.version = version` assigned the raw form value: ProfileSpec
    validates `version` on construction, not on assignment, so `v1.0` or
    `draft` was persisted — and every later load of the draft raised
    SpecVersionError, bricking it. The route now refuses the value in the form
    instead of storing it."""

    async def _ctx(self, session: AsyncSession):
        from metaseed_hub.ui.spec_builder.access import DraftContext, load_state_for_draft

        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        owner = make_user(tenant=tenant, email="meta-owner@example.org")
        session.add(owner)
        await session.flush()
        draft = make_spec_draft(
            tenant=tenant,
            user=owner,
            name="meta",
            spec_data=SpecBuilderState(spec=_spec()).to_dict(),
        )
        session.add(draft)
        await session.commit()
        builder, loaded = await load_state_for_draft(session, draft.id, owner.id)
        return (
            DraftContext(builder=builder, draft=loaded, user_id=owner.id, tenant_id=tenant.id),
            draft.id,
        )

    async def test_a_malformed_version_is_refused_and_not_stored(
        self, session: AsyncSession
    ) -> None:
        from metaseed_hub.ui.spec_builder.access import load_state_for_draft

        ctx, draft_id = await self._ctx(session)
        endpoint = _draft_endpoint("/profile-metadata", "POST")

        response = await endpoint(
            request=_request("POST"),
            ctx=ctx,
            session=session,
            name="meta",
            version="v1.0",
            display_name="",
            description="",
            ontology="",
            root_entity="Study",
        )

        assert "version" in response.body.decode().lower()
        builder, _ = await load_state_for_draft(session, draft_id, ctx.user_id)
        assert builder.spec is not None, "the draft must still load"
        assert builder.spec.version != "v1.0"

    async def test_a_wellformed_version_is_stored(self, session: AsyncSession) -> None:
        from metaseed_hub.ui.spec_builder.access import load_state_for_draft

        ctx, draft_id = await self._ctx(session)
        endpoint = _draft_endpoint("/profile-metadata", "POST")

        await endpoint(
            request=_request("POST"),
            ctx=ctx,
            session=session,
            name="meta",
            version="2.1",
            display_name="",
            description="",
            ontology="",
            root_entity="Study",
        )

        builder, _ = await load_state_for_draft(session, draft_id, ctx.user_id)
        assert builder.spec.version == "2.1"
