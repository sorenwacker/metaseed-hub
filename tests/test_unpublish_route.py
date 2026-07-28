"""The unpublish route accepts a correctly submitted form.

Shipped broken: the route called ``validate_csrf_token(request)`` without the
form field, and that function only consults an ``X-CSRF-Token`` header
otherwise. Every legitimate attempt got ``403 CSRF validation failed``.

The unit tests for unpublishing called the service function directly and the
CSRF tests only asserted that a *missing* token is rejected, so nothing checked
that a valid one is accepted. That is the gap these close.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.templating import Jinja2Templates
from metaseed.specs.schema import ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec
from metaseed_hub.ui.helpers import get_or_create_csrf_token
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
from tests.factories import make_spec, make_tenant, make_user


def _endpoint():
    """The unpublish route's function, as registered."""
    router = APIRouter()
    register_draft_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    route = next(r for r in router.routes if r.path.endswith("/unpublish"))
    return route.endpoint


def _valid_csrf() -> tuple[Mock, str]:
    """A request whose cookie matches the token a form would carry."""
    request = Mock()
    request.cookies = {}
    request.headers = {}
    token = get_or_create_csrf_token(request)
    request.cookies = {"metaseed_csrf_token": token}
    return request, token


async def _published(session: AsyncSession):
    tenant = make_tenant(slug="unpub001")
    session.add(tenant)
    await session.flush()
    author = make_user(tenant=tenant, email="author@example.org")
    session.add(author)
    await session.flush()
    spec = make_spec(
        tenant=tenant,
        created_by=author,
        spec_data={"spec": ProfileSpec(name="X", version="1.0").model_dump(mode="json")},
    )
    session.add(spec)
    await session.commit()
    return tenant, author, spec


async def test_a_valid_form_token_is_accepted(session: AsyncSession) -> None:
    """The regression: this returned 403 for everyone."""
    tenant, author, spec = await _published(session)
    request, token = _valid_csrf()

    response = await _endpoint()(
        request=request,
        spec_id=spec.id,
        session=session,
        user_ctx=(author.id, tenant.id),
        csrf_token=token,
    )

    assert response.status_code == 302, "unpublishing redirects to the new draft"
    await session.refresh(spec)
    assert spec.deleted_at is not None, "and actually withdraws the spec"


async def test_a_missing_token_is_still_rejected(session: AsyncSession) -> None:
    """Accepting the form field must not weaken the check."""
    tenant, author, spec = await _published(session)
    request = Mock()
    request.cookies = {}
    request.headers = {}

    with pytest.raises(HTTPException) as err:
        await _endpoint()(
            request=request,
            spec_id=spec.id,
            session=session,
            user_ctx=(author.id, tenant.id),
            csrf_token=None,
        )

    assert err.value.status_code == 403
    await session.refresh(spec)
    assert spec.deleted_at is None


async def test_a_mismatched_token_is_rejected(session: AsyncSession) -> None:
    tenant, author, spec = await _published(session)
    request, _token = _valid_csrf()

    with pytest.raises(HTTPException) as err:
        await _endpoint()(
            request=request,
            spec_id=spec.id,
            session=session,
            user_ctx=(author.id, tenant.id),
            csrf_token="not-the-token",
        )

    assert err.value.status_code == 403


async def test_someone_who_may_not_edit_cannot_unpublish(session: AsyncSession) -> None:
    """Authorisation still applies once CSRF passes."""
    tenant, _author, spec = await _published(session)
    outsider_tenant = make_tenant(slug="unpub002")
    session.add(outsider_tenant)
    await session.flush()
    outsider = make_user(tenant=outsider_tenant, email="outsider@example.org")
    session.add(outsider)
    await session.commit()
    request, token = _valid_csrf()

    with pytest.raises(HTTPException) as err:
        await _endpoint()(
            request=request,
            spec_id=spec.id,
            session=session,
            user_ctx=(outsider.id, outsider_tenant.id),
            csrf_token=token,
        )

    assert err.value.status_code == 403
    await session.refresh(spec)
    assert spec.deleted_at is None, "an outsider must not withdraw someone's spec"


async def test_the_spec_is_gone_from_listings_afterwards(session: AsyncSession) -> None:
    from sqlalchemy import select

    tenant, author, spec = await _published(session)
    request, token = _valid_csrf()

    await _endpoint()(
        request=request,
        spec_id=spec.id,
        session=session,
        user_ctx=(author.id, tenant.id),
        csrf_token=token,
    )

    live = (await session.execute(select(Spec).where(Spec.deleted_at.is_(None)))).scalars().all()
    assert live == []
