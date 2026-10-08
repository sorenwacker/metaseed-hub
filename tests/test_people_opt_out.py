"""A person may keep their name and address from one collaboration.

People lists every signed-in member of a collaboration to every other member,
and the Sharing tab suggests them. The choice to be left out is made on the
collaboration's card on People, per collaboration, and holds across sign-ins.
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
    opted_out_of,
    people_in,
    set_opt_out,
)
from metaseed_hub.main import create_app
from metaseed_hub.sharing import record_creator, resource_for
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


async def _get(path: str, token: TokenUser = _TOKEN) -> str:
    """The page as ``token`` sees it. On the test's own loop, like the pool."""
    with _as(token):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="https://test"
        ) as client:
            response = await client.get(path)
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.text


async def _post(path: str, data: dict[str, str], token: TokenUser = _TOKEN) -> httpx.Response:
    with _as(token):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="https://test"
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
    async def test_whoever_opted_out_is_left_out_for_the_others(
        self,
        session: AsyncSession,
        people,  # noqa: F811
    ) -> None:  # noqa: F811
        (_, owner), (_, colleague), _ = people
        await set_opt_out(session, colleague.id, CROPXR, hidden=True)
        await session.commit()

        assert [p.email for p in await people_in(session, CROPXR, viewer_id=owner.id)] == [
            "owner@example.org"
        ]

    async def test_but_still_sees_themselves(self, session: AsyncSession, people) -> None:  # noqa: F811
        (_, owner), (_, colleague), _ = people
        await set_opt_out(session, colleague.id, CROPXR, hidden=True)
        await session.commit()

        listed = [p.email for p in await people_in(session, CROPXR, viewer_id=colleague.id)]
        assert listed == ["colleague@example.org", "owner@example.org"]

    async def test_the_choice_can_be_taken_back(self, session: AsyncSession, people) -> None:  # noqa: F811
        (_, owner), (_, colleague), _ = people
        await set_opt_out(session, colleague.id, CROPXR, hidden=True)
        await set_opt_out(session, colleague.id, CROPXR, hidden=False)
        await session.commit()

        assert await opted_out_of(session, colleague.id) == set()
        assert len(await people_in(session, CROPXR, viewer_id=owner.id)) == 2

    async def test_a_choice_about_a_collaboration_one_is_not_in_is_refused(
        self,
        session: AsyncSession,
        people,  # noqa: F811
    ) -> None:  # noqa: F811
        (_, owner), _, _ = people

        with pytest.raises(NotInCollaborationError):
            await set_opt_out(session, owner.id, OTHER, hidden=True)


class TestThePage:
    async def test_each_card_offers_the_choice_unticked(
        self, session: AsyncSession, app_db
    ) -> None:
        await _signed_in_people(session)

        html = await _get("/hub/people")

        assert 'data-testid="opt-out-tudelft:cropxr"' in html
        assert "Hide my name and email from this collaboration" in html
        assert 'data-testid="opt-out-tudelft:cropxr"\n                   checked' not in html
        assert 'data-testid="hidden-from-others"' not in html

    async def test_ticking_it_hides_you_from_the_others_and_says_so_to_you(
        self, session: AsyncSession, app_db
    ) -> None:
        await _signed_in_people(session)

        card = await _post(f"/hub/people/{CROPXR}/visibility", {"hidden": "1"})

        assert card.status_code == 200, card.text[:300]
        assert CARD in card.text
        assert "checked" in card.text
        assert 'data-testid="hidden-from-others"' in card.text
        assert "u@example.org" in card.text, "you still see yourself"
        # The colleague no longer sees you, on People or among the suggestions.
        assert "u@example.org" not in await _get("/hub/people", COLLEAGUE)
        assert "u@example.org" in await _get("/hub/people"), "and it holds for the next visit"

    async def test_unticking_it_lists_you_again(self, session: AsyncSession, app_db) -> None:
        await _signed_in_people(session)
        await _post(f"/hub/people/{CROPXR}/visibility", {"hidden": "1"})

        card = await _post(f"/hub/people/{CROPXR}/visibility", {})

        assert card.status_code == 200
        assert 'data-testid="hidden-from-others"' not in card.text
        assert "u@example.org" in await _get("/hub/people", COLLEAGUE)

    async def test_the_sharing_tab_stops_suggesting_you(
        self, session: AsyncSession, app_db
    ) -> None:
        me_tenant, me, colleague = await _signed_in_people(session)
        dataset = make_dataset(tenant=me_tenant, profile="ena", version="1.0")
        session.add(dataset)
        await session.flush()
        await record_creator(session, resource_for("dataset"), dataset, colleague.id)
        await session.commit()
        before = await _get(f"/hub/sharing/dataset/{dataset.id}/members", COLLEAGUE)
        assert "u@example.org" in before

        await _post(f"/hub/people/{CROPXR}/visibility", {"hidden": "1"})

        after = await _get(f"/hub/sharing/dataset/{dataset.id}/members", COLLEAGUE)
        assert "u@example.org" not in after

    async def test_a_collaboration_you_are_not_in_is_refused(
        self, session: AsyncSession, app_db
    ) -> None:
        await _signed_in_people(session)

        assert (await _post(f"/hub/people/{OTHER}/visibility", {"hidden": "1"})).status_code == 404
