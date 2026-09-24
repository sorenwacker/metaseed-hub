"""When the hub last read a person's collaborations, and what it found.

Membership was recorded only by the sign-in callback, and a token refresh does
not re-run it. A browser session that predated the feature, or simply kept
refreshing for the thirty days the refresh cookie lasts, therefore never had a
record written -- and the page told the person their identity provider had
reported no collaboration, which it never had. Those are different states and
the hub now keeps them apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.collaborations import (
    MembershipState,
    collaborations_of,
    entitled_urns_of,
    membership_state,
    record_memberships,
)
from metaseed_hub.ui.dependencies import ensure_tenant_and_user, tenant_slug_for
from tests.factories import make_tenant, make_user

pytestmark = pytest.mark.asyncio

PREFIX = "urn:mace:surf.nl:sram:group:"
PHENO = PREFIX + "tudelft:cropxr:phenotyping"
CROPXR = PREFIX + "tudelft:cropxr"


async def _person(session: AsyncSession, slug: str):
    sub = f"{slug}-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(user)
    await session.flush()
    await session.commit()
    return tenant, user, sub


async def test_never_asked_is_not_the_same_as_asked_and_told_nothing(
    session: AsyncSession,
) -> None:
    _t, never, _sub = await _person(session, "never")
    _t2, empty, _sub2 = await _person(session, "empty")
    await record_memberships(session, empty.id, [])
    await session.commit()

    assert await membership_state(session, never.id) is MembershipState.NEVER_READ
    assert await membership_state(session, empty.id) is MembershipState.NONE_REPORTED


async def test_a_reading_that_found_groups_is_current(session: AsyncSession) -> None:
    _t, user, _sub = await _person(session, "current")
    await record_memberships(session, user.id, [PHENO])
    await session.commit()

    assert await membership_state(session, user.id) is MembershipState.CURRENT


async def test_an_old_reading_is_stale_and_grants_nothing(session: AsyncSession) -> None:
    _t, user, _sub = await _person(session, "stale")
    await record_memberships(
        session, user.id, [PHENO], seen_at=datetime.now(UTC) - timedelta(days=31)
    )
    await session.commit()

    assert await membership_state(session, user.id) is MembershipState.STALE
    assert await entitled_urns_of(session, user.id) == set()
    assert await collaborations_of(session, user.id) == []


async def test_a_browser_request_records_what_the_session_carries(
    session: AsyncSession,
) -> None:
    """The fix for a session older than the feature: the callback is no longer
    the only chance to be recorded."""
    _t, user, sub = await _person(session, "topup")
    token = TokenUser(sub=sub, email=user.email, name="T", roles=[], entitlements=[PHENO])

    await ensure_tenant_and_user(session, token)

    assert await membership_state(session, user.id) is MembershipState.CURRENT
    assert await entitled_urns_of(session, user.id) == {PHENO, CROPXR}


async def test_a_credential_carrying_no_entitlements_never_erases_a_record(
    session: AsyncSession,
) -> None:
    """A personal access token carries none by construction. Treating that as
    'you are in nothing' would drop a person's access on their next API call."""
    _t, user, sub = await _person(session, "pat")
    await record_memberships(session, user.id, [PHENO])
    await session.commit()
    token = TokenUser(sub=sub, email=user.email, name="T", roles=[], entitlements=[])

    await ensure_tenant_and_user(session, token)

    assert await entitled_urns_of(session, user.id) == {PHENO, CROPXR}


async def test_a_current_record_is_not_rewritten_on_every_request(
    session: AsyncSession,
) -> None:
    _t, user, sub = await _person(session, "quiet")
    await record_memberships(session, user.id, [PHENO])
    await session.commit()
    before = await _read_at(session, user.id)
    token = TokenUser(sub=sub, email=user.email, name="T", roles=[], entitlements=[PHENO])

    await ensure_tenant_and_user(session, token)

    assert await _read_at(session, user.id) == before


async def _read_at(session: AsyncSession, user_id: str):
    from metaseed_hub.models import User

    session.expire_all()
    found = await session.get(User, user_id)
    assert found is not None
    return found.memberships_read_at


# --- what the pages say -----------------------------------------------------


def _page(path: str, sub: str, entitlements: list[str] | None = None) -> str:
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient
    from starlette.routing import Mount

    from metaseed_hub.main import create_app
    from metaseed_hub.ui.dependencies import get_current_user_from_cookie

    token = TokenUser(
        sub=sub,
        email=f"{sub}@example.org",
        name="U",
        roles=[],
        entitlements=entitlements or [],
    )
    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: token
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=token),
    ):
        response = TestClient(app).get(path)
    assert response.status_code == 200, response.status_code
    return response.text


@pytest.mark.parametrize("path", ["/hub/auth/profile", "/hub/people"])
async def test_a_page_tells_someone_never_read_to_sign_in_again(
    session: AsyncSession, app_db, path: str
) -> None:
    """The bug this fixes: the page said the identity provider had reported no
    collaboration, when it had never been asked."""
    _t, _user, sub = await _person(session, "pagenever")

    html = _page(path, sub)

    assert 'data-testid="memberships-never-read"' in html
    assert "sign in again" in html.lower()
    assert "reported no collaboration" not in html


@pytest.mark.parametrize("path", ["/hub/auth/profile", "/hub/people"])
async def test_a_page_says_so_when_the_provider_reported_none(
    session: AsyncSession, app_db, path: str
) -> None:
    _t, user, sub = await _person(session, "pagenone")
    await record_memberships(session, user.id, [])
    await session.commit()

    html = _page(path, sub)

    assert 'data-testid="no-collaborations"' in html
    assert 'data-testid="memberships-never-read"' not in html


async def test_opening_a_page_with_a_live_session_records_the_reading(
    session: AsyncSession, app_db
) -> None:
    """A person whose session predated the feature sees their collaborations
    without signing out, because opening a page takes the reading."""
    _t, user, sub = await _person(session, "pagetopup")
    user_id = user.id

    html = _page("/hub/auth/profile", sub, entitlements=[PHENO])

    assert 'data-testid="collaboration-tudelft:cropxr"' in html
    # The request used its own session; this one still holds the row as it was.
    session.expire_all()
    assert await membership_state(session, user_id) is MembershipState.CURRENT
