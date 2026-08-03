"""Creating a draft under a name you already used is a message, not a 500.

``uq_spec_drafts_tenant_user_name`` keeps one draft name per user, but the
create route let the IntegrityError escape, so the builder answered "Error
creating specification" with a 500 and no hint that the name was the problem.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.spec_builder.routes.list_routes import register_list_routes
from tests.factories import make_tenant, make_user

pytestmark = pytest.mark.asyncio


def _create_endpoint():
    """Resolve the POST /new endpoint from the list router."""
    router = APIRouter()
    register_list_routes(router, Mock())
    for route in router.routes:
        if getattr(route, "path", "") == "/new" and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("no POST /new route")


async def _account(session: AsyncSession):
    sub = f"sub-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub)
    session.add(user)
    await session.commit()
    return tenant, user


async def test_a_second_draft_with_the_same_name_is_refused_not_a_500(
    session: AsyncSession,
) -> None:
    tenant, user = await _account(session)
    create = _create_endpoint()

    first = await create(
        request=Mock(),
        session=session,
        user_ctx=(user.id, tenant.id),
        name="cropxr-phenotyping",
        template="",
    )
    assert first.status_code == 302

    # The same name again must not raise; the user gets told what is wrong.
    second = await create(
        request=Mock(),
        session=session,
        user_ctx=(user.id, tenant.id),
        name="cropxr-phenotyping",
        template="",
    )

    # The duplicate is refused by the constraint either way; what was broken is
    # that the refusal reached the user as a 500 and an unactionable message.
    assert second.status_code == 409
    assert b"already have a specification named" in second.body
    assert b"cropxr-phenotyping" in second.body
