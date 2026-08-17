"""A malformed comment id is a 404, not a database error.

`SpecComment.id` and `SpecComment.parent_id` are UUID-typed columns. Querying
them with a value like "abc" raises a DBAPIError on Postgres — a 500 for what
is simply an id that cannot exist. SQLite, which the tests run on, coerces the
value instead and returns no rows, so the deployment fails where the suite
passes.

The dataset comment routes already guard exactly this (`uuid.UUID(parent_id)`
before the query, with a comment saying why); the spec-builder copies had
diverged. These tests hold the two together by asserting the guard runs
*before* the database is touched, which is checkable on any backend.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from metaseed_hub.ui.spec_builder.routes.comment_routes import register_comment_routes


class _RefusesToQuery:
    """A session that fails the test if the guard lets a query through."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a malformed id must be refused before the query")

    async def commit(self) -> None:
        raise AssertionError("a malformed id must not reach a commit")


def _endpoints() -> dict[str, Any]:
    router = APIRouter()
    register_comment_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    return {route.name: route.endpoint for route in router.routes}


@pytest.fixture(autouse=True)
def _draft_access_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Access is not what is under test; let every request past it."""
    import metaseed_hub.ui.spec_builder.routes.comment_routes as module

    async def _allow(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(module, "require_draft_access", _allow)


@pytest.mark.asyncio
async def test_a_malformed_parent_id_never_reaches_the_query() -> None:
    response = await _endpoints()["add_spec_comment"](
        request=None,
        draft_id="d-1",
        session=_RefusesToQuery(),
        user_ctx=("u-1", None),
        content="hello",
        parent_id="not-a-uuid",
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_malformed_comment_id_never_reaches_the_delete_query() -> None:
    response = await _endpoints()["delete_spec_comment"](
        request=None,
        draft_id="d-1",
        comment_id="not-a-uuid",
        session=_RefusesToQuery(),
        user_ctx=("u-1", None),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_malformed_comment_id_never_reaches_the_reaction_query() -> None:
    response = await _endpoints()["react_to_spec_comment"](
        request=None,
        draft_id="d-1",
        comment_id="not-a-uuid",
        session=_RefusesToQuery(),
        user_ctx=("u-1", None),
        reaction="like",
    )

    assert response.status_code == 404
