"""Restoring a dataset to an earlier version.

Restore is the only path that writes ``Dataset.data`` from a source other than
the live editing state, so it is the one place where the saved tree and the
version history can diverge. These tests assert on the restored *content* — what
the editor loads afterwards — not merely that the route returned 200. They drive
the same helpers the routes use (``ensure_dataset_facade`` to load,
``save_dataset_state`` to persist), since a restore that only satisfies a
different load path is not a restore the user can see.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, DatasetVersion, Tenant
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.helpers.dataset_state import (
    ensure_dataset_facade,
    save_dataset_state,
)
from metaseed_hub.ui.routes.dataset.versions import restore_dataset_version
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))


def _csrf_request() -> Mock:
    """A request carrying a matching CSRF cookie and header, as the browser does."""
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


def _template_request(path: str) -> Request:
    """A real request, since rendering a template needs more than a mock."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [(b"cookie", f"{CSRF_TOKEN_COOKIE}={_CSRF}".encode())],
        }
    )


def _titles(data: dict) -> list[str]:
    """Entity titles in a serialized dataset tree, for content assertions."""
    titles: list[str] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            title = (node.get("data") or {}).get("title")
            if title:
                titles.append(title)
            walk(node.get("children") or [])

    walk(data.get("tree") or [])
    return titles


async def _dataset_with_two_versions(
    session: AsyncSession,
) -> tuple[Dataset, TokenUser]:
    """A miappe dataset saved twice: v1 titled "first", v2 titled "second"."""
    sub = "caller01-restore"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub, email="caller@example.org"))
    dataset = make_dataset(tenant=tenant, profile="miappe", version="1.1")
    session.add(dataset)
    await session.commit()
    token = TokenUser(sub=sub, email="caller@example.org", name="Caller", roles=[])

    state = await ensure_dataset_facade(dataset, session)
    node = state.add_node("Investigation", {"unique_id": "I1", "title": "first"})
    await save_dataset_state(session, dataset, state, token)

    state = await ensure_dataset_facade(dataset, session)
    state.update_node(node.id, {"unique_id": "I1", "title": "second"})
    await save_dataset_state(session, dataset, state, token)

    return dataset, token


async def _versions(session: AsyncSession, dataset_id: str) -> list[DatasetVersion]:
    result = await session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_number)
    )
    return list(result.scalars().all())


async def test_two_saves_record_two_distinct_versions(session: AsyncSession) -> None:
    """Restore can only work if each save is captured; guard that first."""
    dataset, _ = await _dataset_with_two_versions(session)

    versions = await _versions(session, dataset.id)
    assert [v.version_number for v in versions] == [1, 2]
    assert _titles(versions[0].data) == ["first"]
    assert _titles(versions[1].data) == ["second"]


async def test_restoring_an_earlier_version_brings_back_its_content(
    session: AsyncSession,
) -> None:
    dataset, token = await _dataset_with_two_versions(session)
    first = (await _versions(session, dataset.id))[0]

    response = await restore_dataset_version(
        request=_csrf_request(),
        dataset_id=dataset.id,
        version_id=first.id,
        session=session,
        user=token,
    )

    assert response.status_code == 200
    await session.refresh(dataset)
    assert _titles(dataset.data) == [
        "first"
    ], "the dataset must hold the restored version's content"


async def test_restore_is_visible_to_the_editor(session: AsyncSession) -> None:
    """The restored data must survive the load path the editor actually uses."""
    dataset, token = await _dataset_with_two_versions(session)
    first = (await _versions(session, dataset.id))[0]

    await restore_dataset_version(_csrf_request(), dataset.id, first.id, session, token)

    await session.refresh(dataset)
    state = await ensure_dataset_facade(dataset, session)
    titles = [getattr(node.instance, "title", None) for node in state.nodes_by_id.values()]
    assert titles == ["first"]


async def test_restore_records_a_new_version_rather_than_rewriting_history(
    session: AsyncSession,
) -> None:
    dataset, token = await _dataset_with_two_versions(session)
    first = (await _versions(session, dataset.id))[0]

    await restore_dataset_version(_csrf_request(), dataset.id, first.id, session, token)

    versions = await _versions(session, dataset.id)
    assert [v.version_number for v in versions] == [1, 2, 3]
    assert _titles(versions[2].data) == ["first"]


async def test_restoring_the_current_state_changes_nothing_and_says_so(
    session: AsyncSession,
) -> None:
    """A version records the state *after* a save, so the newest one always
    equals the current data. Restoring it cannot change anything; it must not
    add a duplicate version whose diff is empty, and the user must be told.
    """
    dataset, token = await _dataset_with_two_versions(session)
    newest = (await _versions(session, dataset.id))[-1]

    response = await restore_dataset_version(_csrf_request(), dataset.id, newest.id, session, token)

    assert [v.version_number for v in await _versions(session, dataset.id)] == [
        1,
        2,
    ], "a no-op restore must not record a version"
    assert "nothing to restore" in response.body.decode().lower()


async def test_the_version_matching_the_current_state_is_marked_current(
    session: AsyncSession,
) -> None:
    """The list must not offer a Restore control that provably does nothing."""
    from metaseed_hub.ui.app import create_hub_app
    from metaseed_hub.ui.routes.dataset.versions import get_dataset_versions

    create_hub_app()  # binds the Jinja environment the route renders through
    dataset, token = await _dataset_with_two_versions(session)
    versions = await _versions(session, dataset.id)

    response = await get_dataset_versions(
        _template_request(f"/hub/datasets/{dataset.id}/versions"),
        dataset.id,
        session,
        token,
    )
    html = response.body.decode()

    assert (
        f"{versions[1].id}/restore" not in html
    ), "the current version must not offer a restore that does nothing"
    assert f"{versions[0].id}/restore" in html, "older versions stay restorable"


async def test_restoring_a_version_of_another_dataset_is_refused(
    session: AsyncSession,
) -> None:
    """A version id must be checked against the dataset in the path.

    Without the check, a version id from any dataset would overwrite this one.
    """
    dataset, token = await _dataset_with_two_versions(session)
    tenant = await session.get(Tenant, dataset.tenant_id)
    other = make_dataset(tenant=tenant, profile="miappe", version="1.1")
    session.add(other)
    await session.commit()
    state = await ensure_dataset_facade(other, session)
    state.add_node("Investigation", {"unique_id": "I2", "title": "elsewhere"})
    await save_dataset_state(session, other, state, token)
    foreign = (await _versions(session, other.id))[0]

    response = await restore_dataset_version(
        _csrf_request(), dataset.id, foreign.id, session, token
    )

    assert response.status_code == 404
    await session.refresh(dataset)
    assert _titles(dataset.data) == ["second"]
