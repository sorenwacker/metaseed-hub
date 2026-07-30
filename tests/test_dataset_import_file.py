"""Tests for file-based dataset import routes.

Pins that importing a file in the export format ({"entities": [...]}, as
produced by MetaseedClient.serialize) round-trips, that untyped payloads
default to the profile's root entity type, and that parse and example-load
failures degrade to safe user-facing messages.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
import yaml
from fastapi import UploadFile
from metaseed import MetaseedClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade
from metaseed_hub.ui.routes.dataset.crud import dataset_import, dataset_load_example
from metaseed_hub.ui.routes.dataset.editor import dataset_import_into_existing
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


def _upload(content: bytes, filename: str) -> UploadFile:
    return UploadFile(file=BytesIO(content), filename=filename)


async def _caller(session: AsyncSession) -> TokenUser:
    sub = f"importer-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub))
    await session.commit()
    return TokenUser(sub=sub, email="i@example.org", name="I", roles=[])


async def _dataset_with_caller(session: AsyncSession) -> tuple[Dataset, TokenUser]:
    sub = f"importer-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub))
    dataset = make_dataset(tenant=tenant, profile="miappe", version="1.1")
    session.add(dataset)
    await session.commit()
    return dataset, TokenUser(sub=sub, email="i@example.org", name="I", roles=[])


async def test_import_of_export_format_round_trips(session: AsyncSession) -> None:
    """A serialize()-format file previously created an empty dataset silently."""
    client = MetaseedClient("miappe", "1.1")
    inv = client.create_entity("Investigation", {"title": "Exported"}, skip_validation=True)
    client.create_entity("Study", {"title": "S1"}, parent_id=inv.id, skip_validation=True)
    payload = json.dumps(client.serialize()).encode()

    token = await _caller(session)
    name = f"roundtrip-{uuid4().hex[:6]}"
    response = await dataset_import(
        _csrf_request(),
        session,
        token,
        file=_upload(payload, "export.json"),
        name=name,
        csrf_token=_CSRF,
    )

    assert response.status_code == 303
    dataset = (await session.execute(select(Dataset).where(Dataset.name == name))).scalar_one()
    state = await ensure_dataset_facade(dataset, session)
    types = sorted(n.entity_type for n in state.nodes_by_id.values())
    assert types == ["Investigation", "Study"]


async def test_import_into_existing_defaults_untyped_to_root_entity(
    session: AsyncSession,
) -> None:
    """Payloads without a _type marker previously grouped under the profile
    name, which is never an entity type, and were silently dropped."""
    dataset, token = await _dataset_with_caller(session)
    payload = yaml.safe_dump({"entities": [{"title": "Untyped root"}]}).encode()

    response = await dataset_import_into_existing(
        _csrf_request(), dataset.id, session, token, file=_upload(payload, "import.yaml")
    )

    assert response.status_code == 200
    assert "imported 1" in response.body.decode().lower()
    await session.refresh(dataset)
    state = await ensure_dataset_facade(dataset, session)
    assert [n.entity_type for n in state.nodes_by_id.values()] == ["Investigation"]


async def test_parse_error_is_escaped(session: AsyncSession) -> None:
    """Parse exceptions embed file excerpts, so their text must be escaped."""
    dataset, token = await _dataset_with_caller(session)
    payload = yaml.safe_dump({"entities": [{"title": "x"}]}).encode()

    with patch(
        "metaseed_hub.ui.routes.dataset.editor.group_entities_by_type",
        side_effect=ValueError("<script>alert(1)</script>"),
    ):
        response = await dataset_import_into_existing(
            _csrf_request(), dataset.id, session, token, file=_upload(payload, "bad.yaml")
        )

    assert response.status_code == 400
    body = response.body.decode()
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


async def test_load_example_failure_returns_no_traceback(session: AsyncSession) -> None:
    """The traceback previously ended up in the browser, paths and all."""
    dataset, token = await _dataset_with_caller(session)

    with patch(
        "metaseed_hub.ui.routes.dataset.crud.add_entity_node",
        side_effect=RuntimeError("boom at /internal/path.py"),
    ):
        response = await dataset_load_example(_csrf_request(), dataset.id, session, token)

    assert response.status_code == 500
    body = response.body.decode()
    assert "Traceback" not in body
    assert "/internal/path.py" not in body
    assert "Could not load the example dataset" in body
