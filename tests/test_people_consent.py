"""A person is shown to a collaboration only after answering yes.

People lists a collaboration's signed-in members to every other member, and
the Sharing tab suggests them, but only those who answered yes to the question
the hub asks, on the landing page and on the collaboration's card, until it is
answered. A no is recorded too, so it is not asked again. Per collaboration,
kept across sign-ins. The opt-out of 0.64.0 was inverted: the owner wanted
people to opt in, and the undecided to be asked.
See docs/collaboration.md, *People in your collaborations*.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.collaborations import (
    NotInCollaborationError,
    decisions_of,
    people_in,
    set_consent,
    unanswered,
)
from metaseed_hub.main import create_app
from metaseed_hub.sharing import record_creator, resource_for
from metaseed_hub.ui.dependencies import get_current_user_from_cookie
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE
from tests.factories import make_dataset
from tests.test_collaboration_pages import _TOKEN, _signed_in_people
from tests.test_collaborations import (
    CROPXR,
    OTHER,
    people,  # noqa: F401
)

COLLEAGUE = TokenUser(sub="kc-2", email="c@example.org", name="Colleague", roles=[])
CARD = 'data-testid="collaboration-tudelft:cropxr"'


def _as(token: TokenUser):
    return patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=token),
    )


def _app(token: TokenUser):
    """The app with ``token`` signed in on every route.

    The patch reaches routes that call the cookie reader; a route that takes
    ``OptionalUser`` holds the reader as a dependency bound at import, which
    only an override on the mounted hub app replaces."""
    from starlette.routing import Mount

    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: token
    return app


async def _get(path: str, token: TokenUser = _TOKEN) -> str:
    """The page as ``token`` sees it. On the test's own loop, like the pool."""
    with _as(token):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(token)), base_url="https://test"
        ) as client:
            response = await client.get(path)
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


async def _post(path: str, data: dict[str, str], token: TokenUser = _TOKEN) -> httpx.Response:
    with _as(token):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(token)), base_url="https://test"
        ) as client:
            page = await client.get("/hub/people")
            csrf = page.cookies[CSRF_TOKEN_COOKIE]
            return await client.post(
                path,
                data=data,
                cookies=page.cookies,
                headers={"X-CSRF-Token": csrf, "Origin": "https://test"},
            )


class TestTheRule:
    async def test_nobody_is_shown_until_they_answer_yes(
        self,
        session: AsyncSession,
        people,  # noqa: F811
    ) -> None:
        (_, owner), (_, colleague), _ = people

        assert [p.email for p in await people_in(session, CROPXR, viewer_id=owner.id)] == [
            "owner@example.org"
        ], "the viewer alone, whose own row is where the answer is given"
        assert [c.urn for c in await unanswered(session, colleague.id)] == [CROPXR]

    async def test_a_yes_shows_you_to_the_others_and_closes_the_question(
        self,
        session: AsyncSession,
        people,  # noqa: F811
    ) -> None:
        (_, owner), (_, colleague), _ = people
        await set_consent(session, colleague.id, CROPXR, shown=True)
        await session.commit()

        listed = [p.email for p in await people_in(session, CROPXR, viewer_id=owner.id)]
        assert listed == ["colleague@example.org", "owner@example.org"]
        assert await decisions_of(session, colleague.id) == {CROPXR: True}
        assert await unanswered(session, colleague.id) == []

    async def test_a_no_is_an_answer_too(
        self,
        session: AsyncSession,
        people,  # noqa: F811
    ) -> None:
        (_, owner), (_, colleague), _ = people
        await set_consent(session, colleague.id, CROPXR, shown=False)
        await session.commit()

        assert await decisions_of(session, colleague.id) == {CROPXR: False}
        assert await unanswered(session, colleague.id) == []
        assert [p.email for p in await people_in(session, CROPXR, viewer_id=owner.id)] == [
            "owner@example.org"
        ]

    async def test_an_answer_can_be_changed(
        self,
        session: AsyncSession,
        people,  # noqa: F811
    ) -> None:
        (_, owner), (_, colleague), _ = people
        await set_consent(session, colleague.id, CROPXR, shown=True)
        await set_consent(session, colleague.id, CROPXR, shown=False)
        await session.commit()

        assert await decisions_of(session, colleague.id) == {CROPXR: False}

    async def test_an_answer_about_a_collaboration_one_is_not_in_is_refused(
        self,
        session: AsyncSession,
        people,  # noqa: F811
    ) -> None:
        (_, owner), _, _ = people

        with pytest.raises(NotInCollaborationError):
            await set_consent(session, owner.id, OTHER, shown=True)


class TestThePage:
    async def test_an_unanswered_card_asks_and_shows_you_to_yourself_only(
        self, session: AsyncSession, app_db
    ) -> None:
        await _signed_in_people(session, colleague_shown=False)

        html = await _get("/hub/people")

        assert 'data-testid="consent-question-tudelft:cropxr"' in html
        assert "Share your name and email with this collaboration?" in html
        assert 'data-testid="share-with-tudelft:cropxr"' not in html, (
            "the tick box comes after an answer"
        )
        assert 'data-testid="not-shared"' in html
        assert "c@example.org" not in html, "the colleague has not answered"

    async def test_the_landing_page_asks_until_answered(
        self, session: AsyncSession, app_db
    ) -> None:
        await _signed_in_people(session, colleague_shown=False)
        assert 'data-testid="consent-prompt-tudelft:cropxr"' in await _get("/hub/")

        await _post(f"/hub/people/{CROPXR}/visibility", {"shown": "0"})

        assert 'data-testid="consent-prompts"' not in await _get("/hub/")

    async def test_yes_shows_you_to_the_others(self, session: AsyncSession, app_db) -> None:
        await _signed_in_people(session, colleague_shown=False)
        assert "u@example.org" not in await _get("/hub/people", COLLEAGUE)

        card = await _post(f"/hub/people/{CROPXR}/visibility", {"shown": "1"})

        assert card.status_code == 200, card.text[:300]
        assert CARD in card.text
        assert 'data-testid="share-with-tudelft:cropxr"' in card.text and "checked" in card.text
        assert 'data-testid="consent-question-tudelft:cropxr"' not in card.text
        assert 'data-testid="not-shared"' not in card.text
        assert "u@example.org" in await _get("/hub/people", COLLEAGUE)

    async def test_no_closes_the_question_and_keeps_you_to_yourself(
        self, session: AsyncSession, app_db
    ) -> None:
        await _signed_in_people(session, colleague_shown=False)

        card = await _post(f"/hub/people/{CROPXR}/visibility", {"shown": "0"})

        assert card.status_code == 200
        assert 'data-testid="consent-question-tudelft:cropxr"' not in card.text
        assert 'data-testid="share-with-tudelft:cropxr"' in card.text and "checked" not in card.text
        assert 'data-testid="not-shared"' in card.text
        assert "u@example.org" not in await _get("/hub/people", COLLEAGUE)

    async def test_unticking_the_box_hides_you_again(self, session: AsyncSession, app_db) -> None:
        await _signed_in_people(session, colleague_shown=False)
        await _post(f"/hub/people/{CROPXR}/visibility", {"shown": "1"})

        card = await _post(f"/hub/people/{CROPXR}/visibility", {})

        assert card.status_code == 200
        assert 'data-testid="not-shared"' in card.text
        assert "u@example.org" not in await _get("/hub/people", COLLEAGUE)

    async def test_the_sharing_tab_suggests_you_only_after_a_yes(
        self, session: AsyncSession, app_db
    ) -> None:
        me_tenant, me, colleague = await _signed_in_people(session, colleague_shown=False)
        dataset = make_dataset(tenant=me_tenant, profile="ena", version="1.0")
        session.add(dataset)
        await session.flush()
        await record_creator(session, resource_for("dataset"), dataset, colleague.id)
        await session.commit()
        members = f"/hub/sharing/dataset/{dataset.id}/members"
        assert "u@example.org" not in await _get(members, COLLEAGUE)

        await _post(f"/hub/people/{CROPXR}/visibility", {"shown": "1"})

        assert "u@example.org" in await _get(members, COLLEAGUE)

    async def test_a_collaboration_you_are_not_in_is_refused(
        self, session: AsyncSession, app_db
    ) -> None:
        await _signed_in_people(session, colleague_shown=False)

        assert (await _post(f"/hub/people/{OTHER}/visibility", {"shown": "1"})).status_code == 404
