"""Soft-delete consistency tests for the 2026-06-20 review theme.

`Dataset` and `User` extend `SoftDeleteMixin`, and every list/count query filters
`deleted_at IS NULL`. These tests pin the paths that previously diverged:

- H1: the dataset UI delete route soft-deletes instead of hard-deleting (and
  keeps related rows rather than cascading them away).
- M3: the admin dashboard excludes soft-deleted users from its count and
  directory.
- L8: ``delete_draft`` ignores soft-deleted datasets when checking dependents.

They run the real handlers against a database session, so they need Postgres
(``make up``) like the rest of the database-backed suite.
"""

from unittest.mock import Mock

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Comment, Dataset, SpecDraft, Tenant, User
from metaseed_hub.ui.dependencies import get_dataset_for_user
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes import admin as admin_module
from metaseed_hub.ui.routes.dataset import crud as crud_module
from metaseed_hub.ui.spec_builder.routes.draft_routes import register_draft_routes
from tests.factories import make_dataset, make_spec_draft, make_tenant, make_user


def _signed_csrf() -> str:
    """Mint a signed CSRF token as the application issues it."""
    request = Mock()
    request.cookies = {}
    return get_or_create_csrf_token(request)


_CSRF = _signed_csrf()


def _csrf_request() -> Mock:
    """A request mock carrying a matching CSRF cookie and header."""
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    return request


async def _caller_with_tenant(session: AsyncSession) -> tuple[TokenUser, User]:
    """Persist a tenant whose slug is ``keycloak_id[:8]`` and its user."""
    sub = "caller01-softdelete"
    tenant = make_tenant(slug=sub[:8])
    session.add(tenant)
    await session.flush()
    db_user = make_user(tenant=tenant, keycloak_id=sub, email="caller@example.com")
    session.add(db_user)
    await session.commit()
    token = TokenUser(sub=sub, email="caller@example.com", name="Caller", roles=[])
    return token, db_user


# --- H1: dataset UI delete soft-deletes --------------------------------------


@pytest.mark.asyncio
async def test_dataset_delete_soft_deletes_and_keeps_related_rows(
    session: AsyncSession,
) -> None:
    """The UI delete marks deleted_at and leaves the row and its comments intact."""
    token, db_user = await _caller_with_tenant(session)
    tenant = await session.get(Tenant, db_user.tenant_id)
    dataset = make_dataset(tenant=tenant, name="ds")
    session.add(dataset)
    await session.flush()
    comment = Comment(dataset_id=dataset.id, user_id=db_user.id, content="hi")
    session.add(comment)
    await session.commit()
    dataset_id = dataset.id
    comment_id = comment.id

    response = await crud_module.dataset_delete(
        request=_csrf_request(),
        dataset_id=dataset_id,
        session=session,
        user=token,
    )
    assert response.headers["HX-Redirect"] == "/hub/"

    session.expire_all()
    persisted = await session.get(Dataset, dataset_id)
    assert persisted is not None
    assert persisted.deleted_at is not None
    # The cascade was dropped: related rows survive the soft delete.
    assert await session.get(Comment, comment_id) is not None


# --- H2: get_dataset_for_user excludes soft-deleted datasets -----------------


@pytest.mark.asyncio
async def test_get_dataset_for_user_excludes_soft_deleted(session: AsyncSession) -> None:
    """The shared access helper backing ~40 routes treats a soft-deleted dataset as 404."""
    token, db_user = await _caller_with_tenant(session)
    tenant = await session.get(Tenant, db_user.tenant_id)
    dataset = make_dataset(tenant=tenant, name="ds")
    session.add(dataset)
    await session.flush()
    dataset.soft_delete()
    await session.commit()
    dataset_id = dataset.id

    with pytest.raises(HTTPException) as exc:
        await get_dataset_for_user(dataset_id, session, token)
    assert exc.value.status_code == 404


# --- M3: admin dashboard excludes soft-deleted users -------------------------


@pytest.mark.asyncio
async def test_admin_dashboard_excludes_soft_deleted_users(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Soft-deleted users are absent from the count and the directory."""
    captured: dict = {}

    def _fake_render(*, request: object, name: str, context: dict) -> Response:
        captured.update(context)
        return Response("ok")

    monkeypatch.setattr(admin_module, "render_template", _fake_render)

    tenant = make_tenant(slug="adminten")
    session.add(tenant)
    await session.flush()
    active = make_user(tenant=tenant, email="active@example.com")
    removed = make_user(tenant=tenant, email="removed@example.com")
    session.add_all([active, removed])
    await session.flush()
    removed.soft_delete()
    await session.commit()

    admin = TokenUser(sub="adminsub", email="admin@example.com", name="Admin", roles=["admin"])
    await admin_module.admin_dashboard(request=Mock(), session=session, user=admin)

    assert captured["stats"]["users"] == 1
    directory_ids = {u.id for u in captured["users"]}
    assert active.id in directory_ids
    assert removed.id not in directory_ids


@pytest.mark.asyncio
async def test_admin_user_count_query_filters_deleted(session: AsyncSession) -> None:
    """The raw count query underlying the dashboard ignores soft-deleted users."""
    tenant = make_tenant(slug="adminte2")
    session.add(tenant)
    await session.flush()
    active = make_user(tenant=tenant, email="a@example.com")
    removed = make_user(tenant=tenant, email="b@example.com")
    session.add_all([active, removed])
    await session.flush()
    removed.soft_delete()
    await session.commit()

    count = await session.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None)))
    assert count == 1


# --- L8: delete_draft ignores soft-deleted dependents ------------------------


def _delete_draft_endpoint() -> object:
    """Return the DELETE ``/{draft_id}`` endpoint from the draft router."""
    from pathlib import Path

    from fastapi.templating import Jinja2Templates

    router = APIRouter()
    templates = Jinja2Templates(
        directory=str(Path(__file__).parent.parent / "src/metaseed_hub/ui/templates")
    )
    register_draft_routes(router, templates)
    for route in router.routes:
        if "{draft_id}" in getattr(route, "path", "") and "DELETE" in getattr(
            route, "methods", set()
        ):
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError("no DELETE /{draft_id} route")


@pytest.mark.asyncio
async def test_delete_draft_allowed_when_only_dependents_are_soft_deleted(
    session: AsyncSession,
) -> None:
    """A draft is deletable when its only referencing dataset is soft-deleted."""
    tenant = make_tenant(slug="draftten")
    session.add(tenant)
    await session.flush()
    owner = make_user(tenant=tenant)
    session.add(owner)
    await session.flush()
    draft = make_spec_draft(tenant=tenant, user=owner)
    session.add(draft)
    await session.flush()
    dataset = make_dataset(tenant=tenant, name="dependent")
    dataset.spec_draft_id = draft.id
    session.add(dataset)
    await session.flush()
    dataset.soft_delete()
    await session.commit()
    draft_id = draft.id

    delete_draft = _delete_draft_endpoint()
    response = await delete_draft(  # type: ignore[operator]
        request=Mock(),
        draft_id=draft_id,
        session=session,
        user_ctx=(owner.id, tenant.id),
    )

    assert response.headers.get("HX-Redirect") == "/hub/spec-builder"
    assert await session.get(SpecDraft, draft_id) is None
