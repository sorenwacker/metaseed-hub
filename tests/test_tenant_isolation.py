"""Tenant- and scope-isolation tests for comment and member routes.

These cover the 2026-06-20 review findings where a record (comment, reaction,
member) was resolved by id without confirming it belonged to the dataset/draft
named in the URL or to the caller's tenant:

- M4: ``delete_dataset_comment`` must scope the comment to the URL dataset.
- M5: ``react_to_comment`` must scope the comment to the URL dataset.
- M10: ``delete_spec_comment`` must enforce ``require_draft_access`` and scope
  the comment to the URL draft.
- M12: ``add_spec_member`` must scope the invitee lookup to the caller's tenant.

They exercise the real route handlers against a database session, so they need
Postgres (``make up``); without it they error in the shared session fixture like
the rest of the database-backed suite.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import (
    Comment,
    CommentReaction,
    SpecComment,
    SpecDraftMember,
    Tenant,
    User,
)
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE
from metaseed_hub.ui.routes.dataset import comments as comments_module
from metaseed_hub.ui.spec_builder.routes.comment_routes import register_comment_routes
from metaseed_hub.ui.spec_builder.routes.member_routes import register_member_routes
from tests.factories import make_dataset, make_spec_draft, make_tenant, make_user

# A 43-character token (base64 of 32 bytes) that matches in cookie and header.
_CSRF = "a" * 43


def _csrf_request() -> Mock:
    """A request mock carrying a matching CSRF cookie and header."""
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


async def _caller_with_tenant(session: AsyncSession) -> tuple[TokenUser, User]:
    """Persist a tenant and its user, returning the token and the User row.

    The tenant slug is ``keycloak_id[:8]`` so ``get_tenant_for_user`` resolves it.
    """
    sub = "caller01-isolation"
    tenant = make_tenant(slug=sub[:8])
    session.add(tenant)
    await session.flush()
    db_user = make_user(tenant=tenant, keycloak_id=sub, email="caller@example.com")
    session.add(db_user)
    await session.commit()
    token = TokenUser(sub=sub, email="caller@example.com", name="Caller", roles=[])
    return token, db_user


# --- M4 / M5: dataset comment routes ----------------------------------------


@pytest.mark.asyncio
async def test_delete_comment_scoped_to_url_dataset(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comment is only deleted through the dataset it belongs to."""
    monkeypatch.setattr(
        comments_module, "_get_comments_html", AsyncMock(return_value=Response("ok"))
    )
    token, db_user = await _caller_with_tenant(session)
    tenant = await session.get(Tenant, db_user.tenant_id)
    ds_a = make_dataset(tenant=tenant, name="ds-a")
    ds_b = make_dataset(tenant=tenant, name="ds-b")
    session.add_all([ds_a, ds_b])
    await session.flush()
    comment = Comment(dataset_id=ds_b.id, user_id=db_user.id, content="hi")
    session.add(comment)
    await session.commit()

    # Deleting via the wrong dataset must not remove the comment.
    await comments_module.delete_dataset_comment(
        request=_csrf_request(),
        dataset_id=ds_a.id,
        comment_id=comment.id,
        session=session,
        user=token,
    )
    assert await session.get(Comment, comment.id) is not None

    # Deleting via the owning dataset removes it.
    await comments_module.delete_dataset_comment(
        request=_csrf_request(),
        dataset_id=ds_b.id,
        comment_id=comment.id,
        session=session,
        user=token,
    )
    assert await session.get(Comment, comment.id) is None


@pytest.mark.asyncio
async def test_react_to_comment_scoped_to_url_dataset(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reacting through a foreign dataset is rejected and creates no reaction."""
    monkeypatch.setattr(
        comments_module, "_get_comments_html", AsyncMock(return_value=Response("ok"))
    )
    token, db_user = await _caller_with_tenant(session)
    tenant = await session.get(Tenant, db_user.tenant_id)
    ds_a = make_dataset(tenant=tenant, name="ds-a")
    ds_b = make_dataset(tenant=tenant, name="ds-b")
    session.add_all([ds_a, ds_b])
    await session.flush()
    comment = Comment(dataset_id=ds_b.id, user_id=db_user.id, content="hi")
    session.add(comment)
    await session.commit()

    response = await comments_module.react_to_comment(
        request=_csrf_request(),
        dataset_id=ds_a.id,
        comment_id=comment.id,
        session=session,
        user=token,
        reaction="like",
    )

    assert response.status_code == 404
    result = await session.execute(
        select(CommentReaction).where(CommentReaction.comment_id == comment.id)
    )
    assert result.first() is None


# --- M10 / M12: spec builder routes ------------------------------------------


def _templates() -> Jinja2Templates:
    """A templates object whose rendering is stubbed out for unit isolation."""
    from pathlib import Path

    templates = Jinja2Templates(
        directory=str(Path(__file__).parent.parent / "src/metaseed_hub/ui/templates")
    )
    templates.TemplateResponse = lambda *args, **kwargs: Response("ok")  # type: ignore[method-assign]
    return templates


def _endpoint(register: object, method: str, path_contains: str) -> object:
    """Build a router via ``register`` and return the matching endpoint."""
    router = APIRouter()
    register(router, _templates())  # type: ignore[operator]
    for route in router.routes:
        if path_contains in getattr(route, "path", "") and method in getattr(
            route, "methods", set()
        ):
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError(f"no {method} route matching {path_contains!r}")


@pytest.mark.asyncio
async def test_delete_spec_comment_denies_without_draft_access(
    session: AsyncSession,
) -> None:
    """A user who cannot access the draft cannot delete its comments."""
    owner_tenant = make_tenant(slug="ownersp1")
    session.add(owner_tenant)
    await session.flush()
    owner = make_user(tenant=owner_tenant)
    session.add(owner)
    await session.flush()
    draft = make_spec_draft(tenant=owner_tenant, user=owner)
    session.add(draft)
    await session.flush()
    comment = SpecComment(spec_draft_id=draft.id, user_id=owner.id, content="hi")
    session.add(comment)
    intruder_tenant = make_tenant(slug="intruder")
    session.add(intruder_tenant)
    await session.flush()
    intruder = make_user(tenant=intruder_tenant)
    session.add(intruder)
    await session.commit()

    delete_spec_comment = _endpoint(register_comment_routes, "DELETE", "/comments/")

    with pytest.raises(HTTPException) as exc_info:
        await delete_spec_comment(  # type: ignore[operator]
            request=Mock(),
            draft_id=draft.id,
            comment_id=comment.id,
            session=session,
            user_ctx=(intruder.id, intruder_tenant.id),
        )
    assert exc_info.value.status_code == 403
    assert await session.get(SpecComment, comment.id) is not None


@pytest.mark.asyncio
async def test_delete_spec_comment_scoped_to_url_draft(session: AsyncSession) -> None:
    """The draft owner cannot delete a comment that lives in another draft."""
    tenant = make_tenant(slug="ownersp2")
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant)
    session.add(owner)
    await session.flush()
    draft_a = make_spec_draft(tenant=tenant, user=owner, name="A")
    draft_b = make_spec_draft(tenant=tenant, user=owner, name="B")
    session.add_all([draft_a, draft_b])
    await session.flush()
    comment = SpecComment(spec_draft_id=draft_b.id, user_id=owner.id, content="hi")
    session.add(comment)
    await session.commit()

    delete_spec_comment = _endpoint(register_comment_routes, "DELETE", "/comments/")

    await delete_spec_comment(  # type: ignore[operator]
        request=Mock(),
        draft_id=draft_a.id,
        comment_id=comment.id,
        session=session,
        user_ctx=(owner.id, tenant.id),
    )
    assert await session.get(SpecComment, comment.id) is not None


@pytest.mark.asyncio
async def test_add_spec_member_lookup_scoped_to_tenant(session: AsyncSession) -> None:
    """A same-email user in another tenant is never resolved as the invitee."""
    owner_tenant = make_tenant(slug="ownersp3")
    other_tenant = make_tenant(slug="othersp3")
    session.add_all([owner_tenant, other_tenant])
    await session.flush()
    owner = make_user(tenant=owner_tenant)
    # Same email exists in both tenants (allowed by uq_users_tenant_email).
    owner_invitee = make_user(tenant=owner_tenant, email="shared@example.com")
    other_invitee = make_user(tenant=other_tenant, email="shared@example.com")
    session.add_all([owner, owner_invitee, other_invitee])
    await session.flush()
    draft = make_spec_draft(tenant=owner_tenant, user=owner)
    session.add(draft)
    await session.commit()

    add_spec_member = _endpoint(register_member_routes, "POST", "/members")

    # Scoped lookup must resolve the owner-tenant invitee, never raise
    # MultipleResultsFound, and never add the other-tenant user.
    await add_spec_member(  # type: ignore[operator]
        request=Mock(),
        draft_id=draft.id,
        session=session,
        user_ctx=(owner.id, owner_tenant.id),
        email="shared@example.com",
    )

    result = await session.execute(
        select(SpecDraftMember).where(SpecDraftMember.spec_draft_id == draft.id)
    )
    members = list(result.scalars().all())
    assert [m.user_id for m in members] == [owner_invitee.id]
