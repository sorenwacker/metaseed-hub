"""Tests that entity route error fragments escape service error messages.

EntityServiceError.user_message can embed user-controlled input (e.g. the
entity type from the URL path), so it must be escaped before landing in a
hand-built HTML fragment.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.routes import entity as entity_module
from metaseed_hub.ui.services import EntityServiceError
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_PAYLOAD = "<script>alert(1)</script>"


class _FailingService:
    """EntityService stub whose state loading fails with a tainted message."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def ensure_state(self) -> None:
        raise EntityServiceError("technical", user_message=f"Unknown entity type: {_PAYLOAD}")


async def _dataset_with_caller(session: AsyncSession) -> tuple[Dataset, TokenUser]:
    sub = f"escaper-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub))
    dataset = make_dataset(tenant=tenant)
    session.add(dataset)
    await session.commit()
    return dataset, TokenUser(sub=sub, email="x@example.org", name="X", roles=[])


async def test_form_route_escapes_service_error_message(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, token = await _dataset_with_caller(session)
    monkeypatch.setattr(entity_module, "EntityService", _FailingService)

    response = await entity_module.dataset_entity_form(Mock(), dataset.id, _PAYLOAD, session, token)

    body = response.body.decode()
    assert _PAYLOAD not in body
    assert "&lt;script&gt;" in body


async def test_edit_route_escapes_service_error_message(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, token = await _dataset_with_caller(session)
    monkeypatch.setattr(entity_module, "EntityService", _FailingService)

    response = await entity_module.dataset_entity_edit(
        Mock(), dataset.id, "some-node", session, token
    )

    body = response.body.decode()
    assert _PAYLOAD not in body
    assert "&lt;script&gt;" in body
