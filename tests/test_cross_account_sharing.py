"""Sharing resolves an invitee who lives in another tenant.

Every account gets its own tenant (``tenant_slug_for`` hashes the OIDC subject),
so an invitee is *always* outside the sharer's tenant. A tenant-scoped invitee
lookup therefore matched nobody and reported "User not found. They must log in
first before you can share." at a person who was already signed in.

Emails are stored lowercased and matched without regard to capitalisation, so
the address a sharer types resolves whatever casing the identity provider sent.
"""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import APIRouter
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import (
    Dataset,
    DatasetMember,
    SpecDraft,
    SpecDraftMember,
    Tenant,
    User,
)
from metaseed_hub.ui.dependencies import (
    DuplicateAccountEmailError,
    ensure_tenant_and_user,
    tenant_slug_for,
)
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes.dataset import members as members_module
from metaseed_hub.ui.spec_builder.routes.member_routes import register_member_routes
from tests.factories import make_dataset, make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio

_CSRF = get_or_create_csrf_token(Mock(cookies={}))


def _csrf_request() -> Mock:
    """A request mock carrying a matching CSRF cookie and header."""
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


@pytest.fixture(autouse=True)
def _fake_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render dataset member partials to a stub, as the members tests do."""

    def _render(*, request: object, name: str, context: dict) -> Response:
        return Response("rendered")

    monkeypatch.setattr(members_module, "render_template", _render)


def _add_spec_member_endpoint() -> object:
    """Resolve the POST ``/members`` spec-draft endpoint from its router."""
    router = APIRouter()
    register_member_routes(router, Jinja2Templates(directory="src/metaseed_hub/ui/templates"))
    for route in router.routes:
        path = getattr(route, "path", "")
        if "/members" in path and "POST" in getattr(route, "methods", set()):
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError("no POST /members route")


async def _own_tenant_user(
    session: AsyncSession, *, email: str | None = None
) -> tuple[Tenant, User, str]:
    """An account in its own tenant, keyed by subject like real provisioning."""
    sub = f"sub-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=email)
    session.add(user)
    await session.flush()
    return tenant, user, sub


async def _spec_members(session: AsyncSession, draft: SpecDraft) -> list[str]:
    """The user ids recorded as members of a draft."""
    result = await session.execute(
        select(SpecDraftMember).where(SpecDraftMember.spec_draft_id == draft.id)
    )
    return [m.user_id for m in result.scalars().all()]


async def _dataset_members(session: AsyncSession, dataset: Dataset) -> list[str]:
    """The user ids recorded as members of a dataset."""
    result = await session.execute(
        select(DatasetMember).where(DatasetMember.dataset_id == dataset.id)
    )
    return [m.user_id for m in result.scalars().all()]


async def test_spec_draft_shares_with_an_account_in_another_tenant(
    session: AsyncSession,
) -> None:
    """The invitee is a separate account, so they are never in the owner's tenant."""
    owner_tenant, owner, _ = await _own_tenant_user(session)
    _, invitee, _ = await _own_tenant_user(session, email="invitee-a@example.org")
    draft = make_spec_draft(tenant=owner_tenant, user=owner)
    session.add(draft)
    await session.commit()

    add_spec_member = _add_spec_member_endpoint()
    await add_spec_member(  # type: ignore[operator]
        request=Mock(),
        draft_id=draft.id,
        session=session,
        user_ctx=(owner.id, owner_tenant.id),
        email="invitee-a@example.org",
    )

    assert await _spec_members(session, draft) == [invitee.id]


async def test_spec_draft_share_matches_email_case_insensitively(
    session: AsyncSession,
) -> None:
    """A sharer types the address as they know it, not as the IdP cased it."""
    owner_tenant, owner, _ = await _own_tenant_user(session)
    _, invitee, _ = await _own_tenant_user(session, email="invitee-b@example.org")
    draft = make_spec_draft(tenant=owner_tenant, user=owner)
    session.add(draft)
    await session.commit()

    add_spec_member = _add_spec_member_endpoint()
    await add_spec_member(  # type: ignore[operator]
        request=Mock(),
        draft_id=draft.id,
        session=session,
        user_ctx=(owner.id, owner_tenant.id),
        email="  Invitee-B@Example.ORG  ",
    )

    assert await _spec_members(session, draft) == [invitee.id]


async def test_spec_draft_share_reports_an_address_with_no_account(
    session: AsyncSession,
) -> None:
    """An address nobody has signed in with adds nobody and warns the sharer."""
    owner_tenant, owner, _ = await _own_tenant_user(session)
    draft = make_spec_draft(tenant=owner_tenant, user=owner)
    session.add(draft)
    await session.commit()

    add_spec_member = _add_spec_member_endpoint()
    response = await add_spec_member(  # type: ignore[operator]
        request=Mock(),
        draft_id=draft.id,
        session=session,
        user_ctx=(owner.id, owner_tenant.id),
        email="nobody@example.org",
    )

    assert await _spec_members(session, draft) == []
    assert "showToast" in response.headers["HX-Trigger"]


async def test_dataset_shares_with_an_account_in_another_tenant(
    session: AsyncSession,
) -> None:
    """The dataset sharing path has the same cross-tenant invitee."""
    owner_tenant, owner, sub = await _own_tenant_user(session)
    _, invitee, _ = await _own_tenant_user(session, email="invitee-c@example.org")
    dataset = make_dataset(tenant=owner_tenant)
    session.add(dataset)
    await session.commit()
    token = TokenUser(sub=sub, email=owner.email, name="O", roles=[])

    await members_module.add_dataset_member(
        _csrf_request(), dataset.id, session, token, email="invitee-c@example.org"
    )

    assert await _dataset_members(session, dataset) == [invitee.id]


async def test_dataset_share_matches_email_case_insensitively(
    session: AsyncSession,
) -> None:
    """Capitalisation must not decide whether a dataset share resolves."""
    owner_tenant, owner, sub = await _own_tenant_user(session)
    _, invitee, _ = await _own_tenant_user(session, email="invitee-d@example.org")
    dataset = make_dataset(tenant=owner_tenant)
    session.add(dataset)
    await session.commit()
    token = TokenUser(sub=sub, email=owner.email, name="O", roles=[])

    await members_module.add_dataset_member(
        _csrf_request(), dataset.id, session, token, email="Invitee-D@Example.org"
    )

    assert await _dataset_members(session, dataset) == [invitee.id]


async def test_provisioning_stores_the_email_lowercased(session: AsyncSession) -> None:
    """Lowercase storage is what keeps one address to one row under the constraint."""
    sub = f"sub-{uuid4().hex[:8]}"
    token = TokenUser(sub=sub, email="Mixed.Case@Example.ORG", name="M", roles=[])

    _, db_user = await ensure_tenant_and_user(session, token)

    assert db_user.email == "mixed.case@example.org"


async def test_a_new_subject_reusing_a_known_email_is_refused_not_a_500(
    session: AsyncSession,
) -> None:
    """One address to one account means a second account cannot claim it.

    An identity provider that reissues subjects (a rebuilt realm) sends a known
    address under a new subject. That must be a stated refusal an admin can act
    on, not an IntegrityError surfacing as a 500 on every authenticated page,
    and never a silent rebind of the existing account to the new subject.
    """
    _, existing, _ = await _own_tenant_user(session, email="returning@example.org")
    await session.commit()
    new_subject = TokenUser(
        sub=f"sub-{uuid4().hex[:8]}", email="Returning@example.org", name="R", roles=[]
    )

    with pytest.raises(DuplicateAccountEmailError):
        await ensure_tenant_and_user(session, new_subject)

    await session.rollback()
    kept = (
        await session.execute(select(User).where(User.email == "returning@example.org"))
    ).scalar_one()
    assert kept.keycloak_id == existing.keycloak_id


async def test_one_account_exists_per_email_address(session: AsyncSession) -> None:
    """Global uniqueness is what makes an unscoped lookup unambiguous."""
    tenant_a = make_tenant(slug="uniq-a")
    tenant_b = make_tenant(slug="uniq-b")
    session.add_all([tenant_a, tenant_b])
    await session.flush()
    session.add(make_user(tenant=tenant_a, email="shared@example.org"))
    await session.flush()
    session.add(make_user(tenant=tenant_b, email="shared@example.org"))

    with pytest.raises(IntegrityError):
        await session.flush()
