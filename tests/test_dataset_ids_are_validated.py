"""A malformed comment or version id is a 404 before any query.

``Comment.id`` and ``DatasetVersion.id`` are UUID columns. The delete and
react comment routes and the version diff and restore routes compared them
with the raw path segment, so on Postgres a value such as ``abc`` was a
DBAPIError, a 500, where the routes mean the 404 they already return for a
well-formed unknown id. ``add_dataset_comment`` guards ``parent_id`` for this
reason and the spec-builder comment routes guard delete and react; the
SQLite-free suite here runs against Postgres, but the test doubles the session
so the guard is what is tested, not the database's reaction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from metaseed_hub.auth import TokenUser
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes.dataset import comments as comments_module
from metaseed_hub.ui.routes.dataset import versions as versions_module

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))
_USER = TokenUser(sub="ids-caller", email="ids@example.org", name="I", roles=[])


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


class _RefusesToQuery:
    """A session that fails the test if the guard lets a query through."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a malformed id must be refused before the query")

    async def commit(self) -> None:
        raise AssertionError("a malformed id must not reach a commit")


@pytest.fixture(autouse=True)
def _dataset_access_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Access is not what is under test; let every request past it."""

    async def _allow(*args: Any, **kwargs: Any) -> Any:
        return Mock(id="d-1")

    monkeypatch.setattr(comments_module, "get_dataset_for_user", _allow)
    monkeypatch.setattr(versions_module, "get_dataset_for_user", _allow)
    monkeypatch.setattr(versions_module, "get_dataset_for_editor", _allow)


async def test_a_malformed_comment_id_never_reaches_the_delete_query() -> None:
    response = await comments_module.delete_dataset_comment(
        _csrf_request(), "d-1", "not-a-uuid", _RefusesToQuery(), _USER
    )
    assert response.status_code == 404


async def test_a_malformed_comment_id_never_reaches_the_react_query() -> None:
    response = await comments_module.react_to_comment(
        _csrf_request(), "d-1", "not-a-uuid", _RefusesToQuery(), _USER, reaction="like"
    )
    assert response.status_code == 404


async def test_a_malformed_version_id_never_reaches_the_diff_query() -> None:
    response = await versions_module.get_version_diff(
        _csrf_request(), "d-1", "not-a-uuid", _RefusesToQuery(), _USER
    )
    assert response.status_code == 404


async def test_a_malformed_version_id_never_reaches_the_restore_query() -> None:
    response = await versions_module.restore_dataset_version(
        _csrf_request(), "d-1", "not-a-uuid", _RefusesToQuery(), _USER
    )
    assert response.status_code == 404
