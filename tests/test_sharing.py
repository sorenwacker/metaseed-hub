"""Sharing behaves the same for every shared thing.

Each rule is asserted once and run against all three kinds. When datasets and
drafts had separate implementations, a rule could hold in one and not the
other, and nobody would know: datasets had curators, drafts had editors, the
documentation claimed they matched, and published specifications had no sharing
at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec, SpecDraft, SpecStatus
from metaseed_hub.sharing import (
    LastOwnerError,
    NoSuchAccountError,
    NotAnOwnerError,
    Role,
    add_member,
    members_of,
    remove_member,
    resource_for,
    role_of,
    set_role,
)
from tests.factories import make_dataset, make_spec, make_tenant, make_user

KINDS = ["dataset", "draft", "spec"]

SPEC_PAYLOAD = {
    "spec": {
        "name": "shared",
        "version": "1.0",
        "root_entity": "Sample",
        "entities": {"Sample": {"description": "a sample", "fields": []}},
    }
}


@pytest.fixture
async def people(session: AsyncSession):
    """An owner and two others, each in their own account."""
    made = []
    for slug, email in (
        ("owner", "owner@example.org"),
        ("second", "second@example.org"),
        ("third", "third@example.org"),
    ):
        tenant = make_tenant(slug=slug)
        session.add(tenant)
        await session.flush()
        user = make_user(tenant=tenant, keycloak_id=f"kc-{slug}", email=email)
        session.add(user)
        made.append((tenant, user))
    await session.commit()
    return made


async def _make(session: AsyncSession, kind: str, tenant, user) -> str:
    """One shared thing of ``kind``, owned by ``user``. Returns its id."""
    if kind == "dataset":
        thing = make_dataset(tenant=tenant, name="shared-thing")
    elif kind == "draft":
        thing = SpecDraft(
            tenant_id=tenant.id,
            user_id=user.id,
            name="shared-draft",
            version="1.0",
            spec_data=SPEC_PAYLOAD,
        )
    else:
        thing = make_spec(
            tenant=tenant,
            created_by=user,
            name="shared",
            version="1.0",
            spec_data=SPEC_PAYLOAD,
            status=SpecStatus.PUBLISHED,
        )
    session.add(thing)
    await session.commit()

    resource = resource_for(kind)
    member = resource.member_model(
        **{resource.foreign_key: thing.id}, user_id=user.id, role=Role.OWNER
    )
    session.add(member)
    await session.commit()
    return str(thing.id)


@pytest.mark.parametrize("kind", KINDS)
class TestTheSameRulesEverywhere:
    async def test_an_owner_shares_by_email(self, session, people, kind) -> None:
        (tenant, owner), (_, second), _ = people
        resource = resource_for(kind)
        thing_id = await _make(session, kind, tenant, owner)

        await add_member(
            session,
            resource,
            thing_id,
            actor_id=owner.id,
            email="SECOND@example.org",  # capitalisation must not matter
            role=Role.EDITOR,
        )

        assert await role_of(session, resource, thing_id, second.id) is Role.EDITOR

    async def test_someone_who_is_not_an_owner_cannot_share(self, session, people, kind) -> None:
        (tenant, owner), (_, second), (_, third) = people
        resource = resource_for(kind)
        thing_id = await _make(session, kind, tenant, owner)
        await add_member(
            session,
            resource,
            thing_id,
            actor_id=owner.id,
            email=second.email,
            role=Role.EDITOR,
        )

        with pytest.raises(NotAnOwnerError):
            await add_member(
                session,
                resource,
                thing_id,
                actor_id=second.id,
                email=third.email,
                role=Role.VIEWER,
            )

    async def test_an_unknown_address_says_what_to_do(self, session, people, kind) -> None:
        (tenant, owner), *_ = people
        thing_id = await _make(session, kind, tenant, owner)

        with pytest.raises(NoSuchAccountError, match="sign in"):
            await add_member(
                session,
                resource_for(kind),
                thing_id,
                actor_id=owner.id,
                email="nobody@example.org",
            )

    async def test_the_last_owner_cannot_be_demoted(self, session, people, kind) -> None:
        (tenant, owner), *_ = people
        resource = resource_for(kind)
        thing_id = await _make(session, kind, tenant, owner)

        with pytest.raises(LastOwnerError):
            await set_role(
                session,
                resource,
                thing_id,
                actor_id=owner.id,
                user_id=owner.id,
                role=Role.VIEWER,
            )

        assert await role_of(session, resource, thing_id, owner.id) is Role.OWNER

    async def test_the_last_owner_cannot_leave(self, session, people, kind) -> None:
        """An ownerless thing can never be shared or deleted again."""
        (tenant, owner), *_ = people
        resource = resource_for(kind)
        thing_id = await _make(session, kind, tenant, owner)

        with pytest.raises(LastOwnerError, match="leave"):
            await remove_member(session, resource, thing_id, actor_id=owner.id, user_id=owner.id)

    async def test_an_owner_may_leave_once_someone_else_owns_it(
        self, session, people, kind
    ) -> None:
        """This is the handover the hub could not do: a colleague takes over."""
        (tenant, owner), (_, second), _ = people
        resource = resource_for(kind)
        thing_id = await _make(session, kind, tenant, owner)

        await add_member(
            session,
            resource,
            thing_id,
            actor_id=owner.id,
            email=second.email,
            role=Role.OWNER,
        )
        await remove_member(session, resource, thing_id, actor_id=owner.id, user_id=owner.id)

        remaining = await members_of(session, resource, thing_id)
        assert [(m.user_id, Role(m.role)) for m in remaining] == [(second.id, Role.OWNER)]

    async def test_sharing_again_changes_the_role_rather_than_failing(
        self, session, people, kind
    ) -> None:
        (tenant, owner), (_, second), _ = people
        resource = resource_for(kind)
        thing_id = await _make(session, kind, tenant, owner)

        await add_member(session, resource, thing_id, actor_id=owner.id, email=second.email)
        await add_member(
            session,
            resource,
            thing_id,
            actor_id=owner.id,
            email=second.email,
            role=Role.EDITOR,
        )

        assert await role_of(session, resource, thing_id, second.id) is Role.EDITOR
        assert len(await members_of(session, resource, thing_id)) == 2


class TestSharingAPublishedSpecification:
    """The gap that had to be filled by hand: a colleague's specification
    needed a new owner, and no interface could give it one."""

    async def test_an_editor_may_edit_it(self, session, people) -> None:
        from metaseed_hub.ui.spec_builder.access import can_edit_spec

        (tenant, owner), (_, second), _ = people
        thing_id = await _make(session, "spec", tenant, owner)

        assert not await can_edit_spec(session, second.id, thing_id)

        await add_member(
            session,
            resource_for("spec"),
            thing_id,
            actor_id=owner.id,
            email=second.email,
            role=Role.EDITOR,
        )

        assert await can_edit_spec(session, second.id, thing_id)

    async def test_a_viewer_may_not(self, session, people) -> None:
        from metaseed_hub.ui.spec_builder.access import can_edit_spec

        (tenant, owner), (_, second), _ = people
        thing_id = await _make(session, "spec", tenant, owner)
        await add_member(
            session,
            resource_for("spec"),
            thing_id,
            actor_id=owner.id,
            email=second.email,
            role=Role.VIEWER,
        )

        assert not await can_edit_spec(session, second.id, thing_id)


class TestTheVocabularyIsOne:
    def test_every_shared_thing_uses_the_same_roles(self) -> None:
        """Three enums said almost the same thing; a dataset had curators and a
        draft had editors, while the interface claimed they matched."""
        from metaseed_hub.models import DatasetRole, SpecDraftRole, SpecRole

        assert DatasetRole is SpecRole is SpecDraftRole is Role
        assert [role.value for role in Role] == ["owner", "editor", "viewer"]

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(KeyError):
            resource_for("something-else")


class TestSpecMembersUseTheSameTable:
    async def test_a_shared_spec_lists_its_members(self, session, people) -> None:
        (tenant, owner), (_, second), _ = people
        resource = resource_for("spec")
        thing_id = await _make(session, "spec", tenant, owner)
        await add_member(
            session,
            resource,
            thing_id,
            actor_id=owner.id,
            email=second.email,
            role=Role.OWNER,
        )

        members = await members_of(session, resource, thing_id)
        assert {m.user.email for m in members} == {owner.email, second.email}
        assert isinstance(await session.get(Spec, thing_id), Spec)


class TestARoleFromTheBrowserIsNotTrusted:
    """The value arrives from a form; an unknown one used to raise ValueError,
    which the browser saw as a 500."""

    def test_an_unknown_role_is_a_400(self) -> None:
        from fastapi import HTTPException

        from metaseed_hub.ui.routes.sharing import _role_or_refuse

        with pytest.raises(HTTPException) as raised:
            _role_or_refuse("superuser")
        assert raised.value.status_code == 400

    def test_a_known_role_passes_through(self) -> None:
        from metaseed_hub.ui.routes.sharing import _role_or_refuse

        assert _role_or_refuse("editor") is Role.EDITOR

    def test_the_word_the_hub_retired_is_not_a_role(self) -> None:
        """A dataset's roles were owner, curator and viewer; curator became
        editor, and the old word must not quietly work."""
        from fastapi import HTTPException

        from metaseed_hub.ui.routes.sharing import _role_or_refuse

        with pytest.raises(HTTPException):
            _role_or_refuse("curator")


class TestEveryPageLoadsTheOnePanel:
    """Four copies of the same markup existed, two of them still pointing at
    routes that no longer exist. These assert each page asks for the shared
    panel instead of carrying its own."""

    @pytest.mark.parametrize(
        "template,expected",
        [
            ("dataset.html", "/hub/sharing/dataset/"),
            ("partials/dataset_overview.html", "/hub/sharing/dataset/"),
            ("spec_builder/base.html", "/hub/sharing/draft/"),
            ("spec_builder/view.html", "/hub/sharing/spec/"),
        ],
    )
    def test_the_page_asks_for_the_shared_panel(self, template: str, expected: str) -> None:
        import pathlib as _pathlib

        root = _pathlib.Path("src/metaseed_hub/ui/templates")
        markup = (root / template).read_text()
        assert expected in markup, f"{template} does not load the sharing panel"
        assert "member-role-select" not in markup, (
            f"{template} carries its own copy of the members markup"
        )

    def test_no_page_points_at_the_routes_that_were_removed(self) -> None:
        import pathlib as _pathlib

        offenders = []
        for path in _pathlib.Path("src/metaseed_hub/ui/templates").rglob("*.html"):
            markup = path.read_text()
            for dead in ('"/hub/datasets/{{ dataset.id }}/members', '/members"'):
                if dead in markup and "/hub/sharing/" not in markup:
                    offenders.append(str(path))
        assert not offenders, f"still calling removed routes: {offenders}"
