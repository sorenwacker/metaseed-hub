"""Tests for dataset membership routes: role value validation."""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, DatasetMember, DatasetRole, User
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes.dataset import members as members_module
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))


def _csrf_request() -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


@pytest.fixture(autouse=True)
def _fake_render(monkeypatch: pytest.MonkeyPatch) -> None:
    def _render(*, request: object, name: str, context: dict) -> Response:
        return Response("rendered")

    monkeypatch.setattr(members_module, "render_template", _render)


async def _owner_with_member(session: AsyncSession) -> tuple[Dataset, User, TokenUser]:
    """A dataset owned by the caller's tenant, with one VIEWER member."""
    sub = f"owner-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant, keycloak_id=sub)
    member_user = make_user(tenant=tenant)
    session.add_all([owner, member_user])
    await session.flush()
    dataset = make_dataset(tenant=tenant)
    session.add(dataset)
    await session.flush()
    session.add(
        DatasetMember(dataset_id=dataset.id, user_id=member_user.id, role=DatasetRole.VIEWER)
    )
    await session.commit()
    token = TokenUser(sub=sub, email=owner.email, name="O", roles=[])
    return dataset, member_user, token


async def _member_role(session: AsyncSession, dataset_id: str, user_id: str) -> DatasetRole:
    from sqlalchemy import select

    result = await session.execute(
        select(DatasetMember).where(
            DatasetMember.dataset_id == dataset_id,
            DatasetMember.user_id == user_id,
        )
    )
    return result.scalar_one().role


async def test_invalid_role_value_is_rejected(session: AsyncSession) -> None:
    """An unknown role value previously raised ValueError (500)."""
    dataset, member_user, token = await _owner_with_member(session)

    response = await members_module.update_dataset_member_role(
        _csrf_request(), dataset.id, member_user.id, session, token, role="superuser"
    )

    assert response.status_code == 400
    assert await _member_role(session, dataset.id, member_user.id) == DatasetRole.VIEWER


async def test_valid_role_value_is_applied(session: AsyncSession) -> None:
    dataset, member_user, token = await _owner_with_member(session)

    response = await members_module.update_dataset_member_role(
        _csrf_request(), dataset.id, member_user.id, session, token, role="curator"
    )

    assert response.status_code == 200
    assert await _member_role(session, dataset.id, member_user.id) == DatasetRole.CURATOR
