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
    record_creator,
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

    # The way production makes a creator the owner, not a row written by hand:
    # a hand-written row once hid that no creation path wrote one for datasets.
    await record_creator(session, resource_for(kind), thing, user.id)
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


class TestAStrangerLearnsNothing:
    """The write paths checked ownership; the read path checked nothing, so any
    signed-in person could list the members — names and email addresses — of any
    dataset, draft or specification whose id they knew."""

    async def test_someone_with_no_access_cannot_list_the_members(self, session, people) -> None:
        from metaseed_hub.sharing import may_see_members

        (tenant, owner), (other_tenant, stranger), _ = people
        thing_id = await _make(session, "dataset", tenant, owner)

        assert not await may_see_members(
            session,
            resource_for("dataset"),
            thing_id,
            user_id=stranger.id,
            tenant_id=other_tenant.id,
        )

    async def test_someone_it_is_shared_with_can(self, session, people) -> None:
        from metaseed_hub.sharing import may_see_members

        (tenant, owner), (other_tenant, second), _ = people
        resource = resource_for("dataset")
        thing_id = await _make(session, "dataset", tenant, owner)
        await add_member(session, resource, thing_id, actor_id=owner.id, email=second.email)

        assert await may_see_members(
            session, resource, thing_id, user_id=second.id, tenant_id=other_tenant.id
        )

    async def test_the_account_it_lives_in_can(self, session, people) -> None:
        """A dataset's own account has no membership row until someone shares
        it, so ownership of the account has to count."""
        from metaseed_hub.sharing import may_see_members

        (tenant, owner), (_, second), _ = people
        resource = resource_for("dataset")
        thing_id = await _make(session, "dataset", tenant, owner)

        # A colleague of the account with no membership row of their own.
        colleague = make_user(
            tenant=tenant, keycloak_id="kc-colleague", email="colleague@example.org"
        )
        session.add(colleague)
        await session.commit()

        assert await may_see_members(
            session, resource, thing_id, user_id=colleague.id, tenant_id=tenant.id
        )


class TestTheCreatorOwnsWhatTheyMake:
    """Nothing wrote an owner row for a new dataset, so its creator had no role:
    no share form, and ``add_member`` refused them. Every creation path now
    records the creator, and this gate keeps a new path from forgetting."""

    async def test_a_recorded_creator_is_an_owner_who_can_share(self, session, people) -> None:
        (tenant, owner), (_, second), _ = people
        dataset = make_dataset(tenant=tenant, name="mine")
        session.add(dataset)
        await record_creator(session, resource_for("dataset"), dataset, owner.id)
        await session.commit()
        resource = resource_for("dataset")
        assert await role_of(session, resource, dataset.id, owner.id) is Role.OWNER
        await add_member(session, resource, dataset.id, actor_id=owner.id, email=second.email)
        assert await role_of(session, resource, dataset.id, second.id) is Role.VIEWER

    async def test_recording_twice_or_nobody_changes_nothing(self, session, people) -> None:
        (tenant, owner), _, _ = people
        dataset = make_dataset(tenant=tenant, name="mine")
        session.add(dataset)
        resource = resource_for("dataset")
        await record_creator(session, resource, dataset, None)
        await record_creator(session, resource, dataset, owner.id)
        await record_creator(session, resource, dataset, owner.id)
        await session.commit()
        assert [m.user_id for m in await members_of(session, resource, dataset.id)] == [owner.id]

    def test_every_dataset_creation_site_records_its_creator(self) -> None:
        """A ``Dataset(`` built to be stored must be followed by ``record_creator``.

        ``proposed = Dataset(`` in the API is a transient row used only to
        validate a payload and is never added to a session.
        """
        import re
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent / "src" / "metaseed_hub"
        missing = []
        for path in src.rglob("*.py"):
            if path.parts[-2] == "models":
                continue
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                match = re.match(r"\s*(\w+) = Dataset\($", line)
                if not match or match.group(1) == "proposed":
                    continue
                window = "\n".join(lines[index : index + 25])
                if "record_creator(" not in window:
                    missing.append(f"{path.relative_to(src)}:{index + 1}")
        assert not missing, f"dataset created without recording its creator: {missing}"
