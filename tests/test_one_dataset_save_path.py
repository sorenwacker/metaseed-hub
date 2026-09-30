"""``save_dataset_state`` is the only writer of ``dataset.data``.

Three paths wrote the column themselves. The REST ``PATCH`` did so without the
row lock and without recording a version, so a client could not revert what
it pushed and a PATCH racing a browser edit was a lost update. A version
restore copied the version's envelope verbatim, unstamped or in the legacy
flat shape. The MCP editing context carried its own copy of the
serialize-stamp-version sequence, which had already diverged. Everything now
loads into a state and hands it to the one function, which also records the
previous contents as a version when no version row holds them.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from metaseed import MetaseedClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.api import api_router
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.models import DatasetVersion
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.helpers import dataset_state as state_module
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade, save_dataset_state
from metaseed_hub.ui.helpers.tree import update_entity_node
from metaseed_hub.ui.services.entity_service import EntityService
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
STATE_MODULE = SRC / "ui" / "helpers" / "dataset_state.py"


def _tree(title: str) -> dict:
    client = MetaseedClient("miappe", "1.2")
    client.create_entity("Investigation", {"unique_id": "INV-1", "title": title})
    return client.serialize(format="tree")


async def _caller(session: AsyncSession, data: dict | None = None):
    sub = f"savepath-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org"))
    dataset = make_dataset(tenant=tenant, profile="miappe", version="1.2", data=data or {})
    session.add(dataset)
    await session.commit()
    return dataset, TokenUser(sub=sub, email=f"{sub}@example.org", name="S", roles=[])


def _api(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _versions(session: AsyncSession, dataset_id: str) -> list[DatasetVersion]:
    rows = await session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_number)
    )
    return list(rows.scalars())


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {}
    request.headers = {}
    token = get_or_create_csrf_token(request)
    request.cookies = {CSRF_TOKEN_COOKIE: token}
    request.headers = {"X-CSRF-Token": token}
    return request


async def test_a_rest_patch_records_a_version_like_every_other_save(session: AsyncSession) -> None:
    """Before: the row was overwritten and nothing could be reverted."""
    dataset, user = await _caller(session, _tree("first"))

    async with _api(session, user) as client:
        patched = await client.patch(f"/api/datasets/{dataset.id}", json={"data": _tree("second")})

    assert patched.status_code == 200, patched.text
    versions = await _versions(session, dataset.id)
    assert [v.data["tree"][0]["data"]["title"] for v in versions] == ["first", "second"], (
        "the unversioned previous contents, then the new contents"
    )
    await session.refresh(dataset)
    assert dataset.data["spec_hash"].startswith("sha256:")


async def test_a_save_first_records_previous_contents_no_version_holds(
    session: AsyncSession,
) -> None:
    """A state written by any earlier path is never lost on the next save."""
    dataset, _user = await _caller(session, _tree("written directly"))
    assert await _versions(session, dataset.id) == []
    state = await ensure_dataset_facade(dataset, session)
    node_id = next(iter(state.nodes_by_id))
    node = state.nodes_by_id[node_id]
    helper = getattr(state.get_or_create_facade(), node.entity_type)
    update_entity_node(state, node_id, {"unique_id": "INV-1", "title": "edited"}, helper)

    await save_dataset_state(session, dataset, state, None)

    titles = [v.data["tree"][0]["data"]["title"] for v in await _versions(session, dataset.id)]
    assert titles == ["written directly", "edited"]


async def test_a_save_does_not_duplicate_a_previous_state_a_version_already_holds(
    session: AsyncSession,
) -> None:
    dataset, _user = await _caller(session)
    state = await ensure_dataset_facade(dataset, session)
    state.facade.add_entity("Investigation", {"unique_id": "INV-1", "title": "one"})
    state.invalidate_cache()
    await save_dataset_state(session, dataset, state, None)
    node_id = next(iter(state.nodes_by_id))
    helper = state.get_or_create_facade().Investigation
    update_entity_node(state, node_id, {"unique_id": "INV-1", "title": "two"}, helper)

    await save_dataset_state(session, dataset, state, None)

    assert [v.version_number for v in await _versions(session, dataset.id)] == [1, 2]


async def test_a_restore_is_saved_through_the_save_path_and_stamped(session: AsyncSession) -> None:
    """Before: the version's envelope was copied verbatim, unstamped."""
    from metaseed_hub.ui.routes.dataset.versions import restore_dataset_version

    dataset, user = await _caller(session, _tree("current"))
    unstamped = copy.deepcopy(_tree("older"))
    unstamped.pop("spec_hash", None)
    session.add(DatasetVersion(dataset_id=dataset.id, version_number=1, data=unstamped))
    await session.commit()
    version_id = (await _versions(session, dataset.id))[0].id

    response = await restore_dataset_version(_csrf_request(), dataset.id, version_id, session, user)

    assert response.status_code == 200, response.body
    await session.refresh(dataset)
    assert dataset.data["tree"][0]["data"]["title"] == "older"
    assert dataset.data["spec_hash"].startswith("sha256:"), "re-stamped, not copied"
    assert [v.data["tree"][0]["data"]["title"] for v in await _versions(session, dataset.id)] == [
        "older",
        "current",
        "older",
    ]


async def test_the_entity_service_does_not_lock_on_read_routes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, user = await _caller(session, _tree("read"))

    async def _refuse(*args, **kwargs):
        raise AssertionError("a read took the dataset row lock")

    monkeypatch.setattr(state_module, "lock_dataset_for_write", _refuse)

    state = await EntityService(session, dataset, user, for_write=False).ensure_state()

    assert state.nodes_by_id


async def test_the_entity_service_locks_before_it_writes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, user = await _caller(session, _tree("write"))
    locked: list[str] = []

    async def _note(session_, dataset_):
        locked.append(dataset_.id)
        await session_.refresh(dataset_)

    monkeypatch.setattr(state_module, "lock_dataset_for_write", _note)

    await EntityService(session, dataset, user).ensure_state()

    assert locked == [dataset.id]


# --- gate -------------------------------------------------------------------


def _offending_writes() -> list[str]:
    found = []
    for path in SRC.rglob("*.py"):
        if path == STATE_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "data"
                        and isinstance(target.value, ast.Name)
                        and "dataset" in target.value.id.lower()
                    ):
                        found.append(
                            f"{path.relative_to(SRC)}:{node.lineno} {target.value.id}.data ="
                        )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "record_version"
            ):
                found.append(f"{path.relative_to(SRC)}:{node.lineno} record_version(")
    return found


def test_the_gate_sees_the_save_path() -> None:
    source = STATE_MODULE.read_text(encoding="utf-8")
    assert "dataset.data = new_data" in source and "record_version(" in source


def test_dataset_data_is_written_only_by_the_save_path() -> None:
    offenders = _offending_writes()
    assert not offenders, "load a state and call save_dataset_state:\n" + "\n".join(offenders)
