"""A picker names a specification once and lets the version selector choose.

A draft is one name at one version, so three versions of a profile produced
three identical entries reading "CropXR phenotyping (SEEK) (Draft)" with no
way to tell them apart, beside a version selector that had one version in it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.explore_routes import _build_explore_catalog, load_profile_spec
from tests.factories import make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio


def _payload(name: str, version: str) -> dict:
    return {
        "spec": {
            "name": name,
            "display_name": name,
            "version": version,
            "root_entity": "Sample",
            "entities": {"Sample": {"description": "a sample", "fields": []}},
        }
    }


async def _drafts(session: AsyncSession, versions: list[str], name: str = "cropxr-pheno"):
    sub = f"pick-{uuid4().hex[:6]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org")
    session.add(user)
    await session.flush()
    for version in versions:
        session.add(
            make_spec_draft(
                tenant=tenant,
                user=user,
                name=name,
                version=version,
                spec_data=_payload(name, version),
            )
        )
    await session.commit()
    return tenant, user, sub


async def test_the_catalog_names_a_draft_once_with_all_its_versions(
    session: AsyncSession,
) -> None:
    _tenant, _user, sub = await _drafts(session, ["1.1", "1.2", "1.3"])
    token = TokenUser(sub=sub, email=f"{sub}@example.org", name="P", roles=[])

    profiles, versions, names = await _build_explore_catalog(session, token)

    keys = [k for k in profiles if k.startswith("draft:")]
    assert len(keys) == 1, keys
    assert versions[keys[0]] == ["1.1", "1.2", "1.3"], (
        "oldest first; the picker defaults to the last"
    )
    assert names[keys[0]] == "cropxr-pheno (Draft)"


async def test_the_version_chooses_which_draft_is_loaded(session: AsyncSession) -> None:
    tenant, user, sub = await _drafts(session, ["1.1", "1.3"])
    token = TokenUser(sub=sub, email=f"{sub}@example.org", name="P", roles=[])
    profiles, _versions, _names = await _build_explore_catalog(session, token)
    key = next(k for k in profiles if k.startswith("draft:"))

    older = await load_profile_spec(session, key, "1.1", tenant.id, user_id=user.id)
    newer = await load_profile_spec(session, key, "1.3", tenant.id, user_id=user.id)

    assert older is not None and older[1].version == "1.1"
    assert newer is not None and newer[1].version == "1.3"


async def test_a_draft_is_still_reachable_by_its_id(session: AsyncSession) -> None:
    """Anything that stored the old key keeps working."""
    from sqlalchemy import select

    from metaseed_hub.models import SpecDraft

    tenant, user, _sub = await _drafts(session, ["1.2"])
    draft = (await session.execute(select(SpecDraft))).scalars().first()
    assert draft is not None

    loaded = await load_profile_spec(
        session, f"draft:{draft.id}", "1.2", tenant.id, user_id=user.id
    )

    assert loaded is not None and loaded[1].version == "1.2"


async def test_the_new_dataset_picker_names_a_draft_once(session: AsyncSession, app_db) -> None:
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient
    from starlette.routing import Mount

    from metaseed_hub.main import create_app
    from metaseed_hub.ui.dependencies import get_current_user_from_cookie

    _tenant, _user, sub = await _drafts(session, ["1.1", "1.2", "1.3"], name="pickonce")
    token = TokenUser(sub=sub, email=f"{sub}@example.org", name="P", roles=[])
    app = create_app()
    hub = next(r.app for r in app.routes if isinstance(r, Mount) and r.path == "/hub")
    hub.dependency_overrides[get_current_user_from_cookie] = lambda: token
    with patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=token),
    ):
        html = TestClient(app).get("/hub/datasets/new").text

    # The page offers the profile as a card and in a select; each must name it
    # once, with its versions on the selector beside it.
    assert html.count('value="draft:pickonce"') == 1, html.count('value="draft:pickonce"')
    assert html.count("pickonce (Draft)") == 2
    assert "1.1" in html and "1.2" in html and "1.3" in html


async def test_creating_a_dataset_binds_the_chosen_version(session: AsyncSession) -> None:
    from sqlalchemy import select

    from metaseed_hub.models import SpecDraft
    from metaseed_hub.ui.routes.dataset.crud import draft_for_choice

    tenant, user, _sub = await _drafts(session, ["1.1", "1.3"], name="bindme")

    chosen = await draft_for_choice(session, "draft:bindme", "1.1", tenant.id, user.id)
    newest = await draft_for_choice(session, "draft:bindme", "1.3", tenant.id, user.id)

    assert chosen is not None and chosen.version == "1.1"
    assert newest is not None and newest.version == "1.3"
    assert chosen.id != newest.id
    assert (await session.execute(select(SpecDraft))).scalars().all()
