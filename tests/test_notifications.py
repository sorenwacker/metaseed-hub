"""The hub tells a person what happened to their items.

Nothing did. A dataset shared with someone appeared in their list without a
word, a role could be taken away unannounced, and a comment sat unread until
its recipient happened to open the item. Each rule in
``docs/collaboration.md#notifications`` is asserted here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub import notifications
from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.models import Comment, Notification, SpecComment, SpecDraft
from metaseed_hub.sharing import (
    Role,
    add_member,
    record_creator,
    remove_member,
    resource_for,
    set_role,
)
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from tests.conftest import _test_database_url
from tests.factories import make_dataset, make_spec, make_tenant, make_user

KINDS = ["dataset", "draft", "spec"]

SPEC_PAYLOAD = {
    "spec": {
        "name": "noted",
        "version": "1.0",
        "root_entity": "Sample",
        "entities": {"Sample": {"description": "a sample", "fields": []}},
    }
}


@pytest.fixture
async def people(session: AsyncSession):
    """An owner and two others, each in their own account."""
    made = []
    for slug, name in (("owner", "Olive Owner"), ("second", "Sam Second"), ("third", "Tess Third")):
        tenant = make_tenant(slug=tenant_slug_for(f"kc-{slug}"))
        session.add(tenant)
        await session.flush()
        user = make_user(
            tenant=tenant, keycloak_id=f"kc-{slug}", email=f"{slug}@example.org", display_name=name
        )
        session.add(user)
        made.append((tenant, user))
    await session.commit()
    return made


async def _make(session: AsyncSession, kind: str, tenant, user) -> str:
    """One shared thing of ``kind``, owned by ``user``. Returns its id."""
    if kind == "dataset":
        thing: Any = make_dataset(tenant=tenant, name="noted-thing")
    elif kind == "draft":
        thing = SpecDraft(
            tenant_id=tenant.id,
            user_id=user.id,
            name="noted-thing",
            version="1.0",
            spec_data=SPEC_PAYLOAD,
        )
    else:
        thing = make_spec(
            tenant=tenant,
            created_by=user,
            name="noted-thing",
            version="1.0",
            spec_data=SPEC_PAYLOAD,
        )
    session.add(thing)
    await record_creator(session, resource_for(kind), thing, user.id)
    await session.commit()
    return str(thing.id)


async def _for(session: AsyncSession, user) -> list[Notification]:
    rows = await session.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


class TestSharing:
    @pytest.mark.parametrize("kind", KINDS)
    async def test_being_given_access_is_notified(self, session, people, kind) -> None:
        (tenant, owner), (_, second), _ = people
        thing = await _make(session, kind, tenant, owner)

        await add_member(
            session,
            resource_for(kind),
            thing,
            actor_id=owner.id,
            email=second.email,
            role=Role.EDITOR,
        )

        (entry,) = await _for(session, second)
        assert entry.kind == notifications.Kind.SHARED
        assert entry.actor_id == owner.id
        assert (entry.resource_kind, entry.resource_id) == (kind, thing)
        assert "noted-thing" in entry.resource_title
        assert entry.detail == "editor"
        assert entry.read_at is None

    async def test_the_person_who_shares_is_not_notified(self, session, people) -> None:
        (tenant, owner), (_, second), _ = people
        thing = await _make(session, "dataset", tenant, owner)
        await add_member(
            session, resource_for("dataset"), thing, actor_id=owner.id, email=second.email
        )
        assert await _for(session, owner) == []

    async def test_a_changed_role_is_notified(self, session, people) -> None:
        (tenant, owner), (_, second), _ = people
        thing = await _make(session, "dataset", tenant, owner)
        resource = resource_for("dataset")
        await add_member(session, resource, thing, actor_id=owner.id, email=second.email)

        await set_role(
            session, resource, thing, actor_id=owner.id, user_id=second.id, role=Role.EDITOR
        )

        kinds = [(n.kind, n.detail) for n in await _for(session, second)]
        assert kinds == [
            (notifications.Kind.SHARED, "viewer"),
            (notifications.Kind.ROLE_CHANGED, "editor"),
        ]

    async def test_setting_the_role_someone_has_says_nothing(self, session, people) -> None:
        (tenant, owner), (_, second), _ = people
        thing = await _make(session, "dataset", tenant, owner)
        resource = resource_for("dataset")
        await add_member(session, resource, thing, actor_id=owner.id, email=second.email)

        await set_role(
            session, resource, thing, actor_id=owner.id, user_id=second.id, role=Role.VIEWER
        )

        assert len(await _for(session, second)) == 1

    async def test_removed_access_is_notified(self, session, people) -> None:
        (tenant, owner), (_, second), _ = people
        thing = await _make(session, "dataset", tenant, owner)
        resource = resource_for("dataset")
        await add_member(session, resource, thing, actor_id=owner.id, email=second.email)

        await remove_member(session, resource, thing, actor_id=owner.id, user_id=second.id)

        assert (await _for(session, second))[-1].kind == notifications.Kind.ACCESS_REMOVED

    async def test_leaving_notifies_nobody(self, session, people) -> None:
        (tenant, owner), (_, second), _ = people
        thing = await _make(session, "dataset", tenant, owner)
        resource = resource_for("dataset")
        await add_member(session, resource, thing, actor_id=owner.id, email=second.email)

        await remove_member(session, resource, thing, actor_id=second.id, user_id=second.id)

        assert [n.kind for n in await _for(session, second)] == [notifications.Kind.SHARED]
        assert await _for(session, owner) == []


def _csrf_request() -> Mock:
    token = get_or_create_csrf_token(Mock(cookies={}))
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: token}
    request.headers = {"X-CSRF-Token": token}
    return request


def _token(user) -> TokenUser:
    return TokenUser(sub=user.keycloak_id, email=user.email, name=user.display_name, roles=[])


class TestComments:
    @pytest.fixture(autouse=True)
    def _no_rendering(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from starlette.responses import Response

        from metaseed_hub.ui.routes.dataset import comments as dataset_comments
        from metaseed_hub.ui.spec_builder.routes import comment_routes

        async def _html(*args: Any, **kwargs: Any) -> Response:
            return Response("rendered")

        async def _allow(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(dataset_comments, "_get_comments_html", _html)
        monkeypatch.setattr(comment_routes, "require_draft_access", _allow)

    async def _shared_dataset(self, session, people) -> str:
        (tenant, owner), (_, second), (_, third) = people
        thing = await _make(session, "dataset", tenant, owner)
        for person in (second, third):
            await add_member(
                session,
                resource_for("dataset"),
                thing,
                actor_id=owner.id,
                email=person.email,
                role=Role.EDITOR,
            )
        return thing

    async def test_a_comment_on_your_dataset_is_notified(self, session, people) -> None:
        from metaseed_hub.ui.routes.dataset.comments import add_dataset_comment

        (_, owner), (_, second), (_, third) = people
        thing = await self._shared_dataset(session, people)

        await add_dataset_comment(_csrf_request(), thing, session, _token(second), content="hello")

        assert [n.kind for n in await _for(session, owner)] == [notifications.Kind.COMMENT]
        assert notifications.Kind.COMMENT not in [n.kind for n in await _for(session, second)]
        assert notifications.Kind.COMMENT not in [n.kind for n in await _for(session, third)], (
            "a member who does not own the dataset is not told of every comment"
        )

    async def test_a_reply_reaches_the_author_of_the_comment(self, session, people) -> None:
        from metaseed_hub.ui.routes.dataset.comments import add_dataset_comment

        (_, owner), (_, second), (_, third) = people
        thing = await self._shared_dataset(session, people)
        root = Comment(dataset_id=thing, user_id=third.id, content="root")
        session.add(root)
        await session.commit()

        await add_dataset_comment(
            _csrf_request(), thing, session, _token(second), content="reply", parent_id=root.id
        )

        assert (await _for(session, third))[-1].kind == notifications.Kind.REPLY
        assert [n.kind for n in await _for(session, owner)] == [notifications.Kind.COMMENT]

    async def test_one_action_is_one_entry_per_person(self, session, people) -> None:
        """The owner whose own comment is replied to gets the reply, once."""
        from metaseed_hub.ui.routes.dataset.comments import add_dataset_comment

        (_, owner), (_, second), _ = people
        thing = await self._shared_dataset(session, people)
        root = Comment(dataset_id=thing, user_id=owner.id, content="root")
        session.add(root)
        await session.commit()

        await add_dataset_comment(
            _csrf_request(), thing, session, _token(second), content="reply", parent_id=root.id
        )

        assert [n.kind for n in await _for(session, owner)] == [notifications.Kind.REPLY]

    async def test_your_own_comment_notifies_nobody(self, session, people) -> None:
        from metaseed_hub.ui.routes.dataset.comments import add_dataset_comment

        (_, owner), _, _ = people
        thing = await self._shared_dataset(session, people)

        await add_dataset_comment(_csrf_request(), thing, session, _token(owner), content="note")

        assert await _for(session, owner) == []

    async def test_a_comment_on_your_draft_is_notified(self, session, people) -> None:
        from metaseed_hub.ui.spec_builder.routes.comment_routes import register_comment_routes

        (tenant, owner), (_, second), _ = people
        thing = await _make(session, "draft", tenant, owner)
        router = APIRouter()
        register_comment_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
        add_spec_comment = {r.name: r.endpoint for r in router.routes}["add_spec_comment"]

        await add_spec_comment(
            request=Request({"type": "http", "method": "POST", "path": "/", "headers": []}),
            draft_id=thing,
            session=session,
            user_ctx=(second.id, None),
            content="looks right",
            parent_id=None,
        )

        (entry,) = await _for(session, owner)
        assert (entry.kind, entry.resource_kind) == (notifications.Kind.COMMENT, "draft")
        assert (await session.execute(select(SpecComment))).scalar_one().content == "looks right"


class TestTheList:
    async def _shared(self, session, people) -> str:
        (tenant, owner), (_, second), _ = people
        thing = await _make(session, "dataset", tenant, owner)
        await add_member(
            session, resource_for("dataset"), thing, actor_id=owner.id, email=second.email
        )
        return thing

    async def test_unread_entries_are_counted(self, session, people) -> None:
        _, (_, second), (_, third) = people
        await self._shared(session, people)
        assert await notifications.unread_count(session, second.id) == 1
        assert await notifications.unread_count(session, third.id) == 0

    async def test_opening_the_list_marks_it_read(self, session, people) -> None:
        _, (_, second), _ = people
        await self._shared(session, people)

        (entry,) = await notifications.open_list(session, second.id)

        assert entry.was_unread
        assert await notifications.unread_count(session, second.id) == 0
        (again,) = await notifications.open_list(session, second.id)
        assert not again.was_unread

    async def test_an_entry_says_what_happened_and_links_to_the_item(self, session, people) -> None:
        _, (_, second), _ = people
        thing = await self._shared(session, people)

        (entry,) = await notifications.open_list(session, second.id)

        assert entry.actor == "Olive Owner"
        assert entry.sentence == "Olive Owner shared the dataset noted-thing with you as viewer."
        assert entry.url == f"/hub/datasets/{thing}"

    async def test_removed_access_has_no_link(self, session, people) -> None:
        (_, owner), (_, second), _ = people
        thing = await self._shared(session, people)
        await remove_member(
            session, resource_for("dataset"), thing, actor_id=owner.id, user_id=second.id
        )

        entries = await notifications.open_list(session, second.id)

        assert entries[0].sentence == "Olive Owner removed your access to the dataset noted-thing."
        assert entries[0].url is None, "the item can no longer be opened"

    async def test_entries_older_than_ninety_days_are_dropped(self, session, people) -> None:
        _, (_, second), _ = people
        await self._shared(session, people)
        (stored,) = await _for(session, second)
        stored.created_at = datetime.now(UTC) - timedelta(days=91)
        await session.commit()

        assert await notifications.open_list(session, second.id) == []
        assert await _for(session, second) == []


def _signed_in_as(user):
    return patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_token(user)),
    )


async def _get(path: str, user) -> httpx.Response:
    app = create_app()
    with _signed_in_as(user):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.get(path)


class TestTheBell:
    @pytest.fixture
    async def app_db(self, session):
        from metaseed_hub.database import db

        await db.connect(_test_database_url())
        yield
        await db.disconnect()

    async def test_the_header_carries_the_bell(self, session, people, app_db) -> None:
        _, (_, second), _ = people
        html = (await _get("/hub/people", second)).text
        assert 'data-testid="notification-bell"' in html
        assert 'href="/hub/notifications"' in html
        assert 'hx-get="/hub/notifications/badge"' in html

    async def test_the_badge_is_the_unread_count(self, session, people, app_db) -> None:
        _, (_, second), (_, third) = people
        await TestTheList()._shared(session, people)
        assert (await _get("/hub/notifications/badge", second)).text.strip() == "1"
        assert (await _get("/hub/notifications/badge", third)).text.strip() == ""

    async def test_the_page_lists_and_clears(self, session, people, app_db) -> None:
        _, (_, second), _ = people
        thing = await TestTheList()._shared(session, people)

        html = (await _get("/hub/notifications", second)).text

        assert "Olive Owner" in html and "noted-thing" in html
        assert f'href="/hub/datasets/{thing}"' in html
        assert 'data-testid="notification-unread"' in html
        assert (await _get("/hub/notifications/badge", second)).text.strip() == ""

    async def test_a_signed_out_page_is_answered_with_nothing(self, session, app_db) -> None:
        """A page left open past its session keeps asking; it must not be sent
        to sign-in by its own bell."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get("/hub/notifications/badge")
        assert response.status_code == 200
        assert response.text == ""

    async def test_nobody_sees_another_persons_entries(self, session, people, app_db) -> None:
        _, _, (_, third) = people
        await TestTheList()._shared(session, people)
        html = (await _get("/hub/notifications", third)).text
        assert "noted-thing" not in html
        assert 'data-testid="notifications-empty"' in html
