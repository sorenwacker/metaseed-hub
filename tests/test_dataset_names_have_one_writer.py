"""A dataset name is written in one place, and a clash is answered, not a 500.

Names are unique per tenant (``uq_datasets_tenant_name``) and a soft-deleted
dataset keeps its name. Six paths set a name. The web form and file import
caught ``IntegrityError`` around ``commit()``, but ``record_creator`` flushes
first, so production logged the violation as a 500; ``POST`` and ``PATCH
/api/datasets`` caught nothing. The paths that did catch it redirected to
``/hub/datasets/new?error=duplicate_name``, a page that displayed no error code
at all -- the user was returned to an empty form with no reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.api import api_router
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session
from metaseed_hub.repositories.datasets import DuplicateDatasetNameError, create_dataset
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes.dataset import crud
from tests.factories import make_dataset, make_tenant, make_user

pytestmark = pytest.mark.asyncio

SRC = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub"
WRITER = SRC / "repositories" / "datasets.py"
_CSRF = get_or_create_csrf_token(Mock(cookies={}))


def _csrf_request(query: dict[str, str] | None = None) -> Mock:
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: _CSRF}
    request.headers = {"X-CSRF-Token": _CSRF}
    request.query_params = query or {}
    return request


async def _caller_holding(
    session: AsyncSession, name: str, *, deleted: bool = False
) -> tuple[str, TokenUser]:
    """A signed-in caller whose tenant already has a dataset called ``name``."""
    sub = f"names-{uuid4().hex[:8]}"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub, email=f"{sub}@example.org"))
    held = make_dataset(tenant=tenant, name=name)
    if deleted:
        held.soft_delete()
    session.add(held)
    await session.commit()
    return tenant.id, TokenUser(sub=sub, email=f"{sub}@example.org", name="N", roles=[])


def _api(session: AsyncSession, user: TokenUser) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    async def _session() -> Any:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _is_duplicate_redirect(response: Any) -> bool:
    return response.status_code == 302 and response.headers["location"].endswith(
        "error=duplicate_name"
    )


# --- every path that sets a name ---------------------------------------------


async def test_the_web_form_answers_a_taken_name_with_the_form(session: AsyncSession) -> None:
    """The production case: a 500 from ``record_creator``'s flush."""
    _tenant, user = await _caller_holding(session, "taken")

    response = await crud.dataset_create(
        _csrf_request(),
        session,
        user,
        name="taken",
        profile="miappe",
        version="1.1",
        csrf_token=_CSRF,
    )

    assert _is_duplicate_redirect(response)


async def test_a_file_import_answers_a_taken_name_with_the_form(session: AsyncSession) -> None:
    _tenant, user = await _caller_holding(session, "taken")

    response = await crud.dataset_import(
        _csrf_request(),
        session,
        user,
        file=UploadFile(file=__import__("io").BytesIO(b'{"entities": []}'), filename="d.json"),
        name="taken",
        profile="miappe",
        version="1.1",
        csrf_token=_CSRF,
    )

    assert _is_duplicate_redirect(response)


async def test_the_rest_api_refuses_a_taken_name_with_409(session: AsyncSession) -> None:
    tenant_id, user = await _caller_holding(session, "taken")

    async with _api(session, user) as client:
        response = await client.post(
            "/api/datasets",
            json={"tenant_id": tenant_id, "name": "taken", "profile": "miappe", "version": "1.1"},
        )

    assert response.status_code == 409
    assert "taken" in response.json()["detail"]


async def test_the_rest_api_refuses_a_rename_onto_a_taken_name_with_409(
    session: AsyncSession,
) -> None:
    tenant_id, user = await _caller_holding(session, "taken")
    async with _api(session, user) as client:
        created = await client.post(
            "/api/datasets",
            json={"tenant_id": tenant_id, "name": "free", "profile": "miappe", "version": "1.1"},
        )
        response = await client.patch(
            f"/api/datasets/{created.json()['id']}", json={"name": "taken"}
        )

    assert response.status_code == 409


async def test_a_name_held_by_a_deleted_dataset_is_refused_with_that_reason(
    session: AsyncSession,
) -> None:
    """The holder is invisible to the user, so the message has to name it."""
    tenant_id, _user = await _caller_holding(session, "recycled", deleted=True)

    with pytest.raises(DuplicateDatasetNameError, match="deleted dataset") as refused:
        await create_dataset(
            session,
            tenant_id=tenant_id,
            name="recycled",
            profile="miappe",
            version="1.1",
            creator_id=None,
        )

    assert refused.value.held_by_deleted


# --- the form says why --------------------------------------------------------


def _error_codes_redirected_to_the_form() -> set[str]:
    """Literal codes in the source, plus the two the name-clash redirect builds."""
    codes = set()
    for path in SRC.rglob("*.py"):
        codes |= set(
            re.findall(r"/hub/datasets/new\?error=([a-z_]+)", path.read_text(encoding="utf-8"))
        )
    for deleted in (False, True):
        location = crud._duplicate_name_redirect(DuplicateDatasetNameError("n", deleted)).headers[
            "location"
        ]
        codes.add(location.split("error=")[1])
    return codes


def test_the_scan_finds_the_error_codes() -> None:
    codes = _error_codes_redirected_to_the_form()
    assert {"import_failed", "duplicate_name", "name_held_by_deleted"} <= codes


def test_every_error_the_form_is_sent_has_a_message() -> None:
    missing = _error_codes_redirected_to_the_form() - set(crud.NEW_DATASET_ERRORS)
    assert not missing, f"redirected to the New Dataset form with no message: {sorted(missing)}"


async def test_the_form_is_given_the_message_for_its_error(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tenant, user = await _caller_holding(session, "taken")
    rendered: dict[str, Any] = {}
    monkeypatch.setattr(crud, "render_template", lambda **kw: rendered.update(kw["context"]))

    await crud.dataset_new(_csrf_request({"error": "duplicate_name"}), session, user)

    assert rendered["error_message"] == crud.NEW_DATASET_ERRORS["duplicate_name"]


def test_the_form_shows_the_message() -> None:
    page = (SRC / "ui" / "templates" / "dataset_new.html").read_text(encoding="utf-8")
    assert "{% if error_message %}" in page
    assert 'data-testid="new-dataset-error"' in page


# --- gate ---------------------------------------------------------------------


def _name_writes() -> list[str]:
    """``Dataset(name=...)`` constructions and ``<dataset>.name = ...`` assignments."""
    found = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Dataset"
                and any(kw.arg == "name" for kw in node.keywords)
            ):
                found.append(f"{path.relative_to(SRC)}:{node.lineno} Dataset(name=...)")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "name"
                        and isinstance(target.value, ast.Name)
                        and "dataset" in target.value.id.lower()
                    ):
                        found.append(
                            f"{path.relative_to(SRC)}:{node.lineno} {target.value.id}.name ="
                        )
    return [f for f in found if not f.startswith(str(WRITER.relative_to(SRC)))]


def test_the_gate_sees_name_writes() -> None:
    """A scan that finds nothing passes vacuously; the writer's own must be seen."""
    source = WRITER.read_text(encoding="utf-8")
    assert "Dataset(" in source and "dataset.name = name" in source


def test_dataset_names_are_written_only_by_the_repository() -> None:
    offenders = _name_writes()
    assert not offenders, "use create_dataset/rename_dataset:\n" + "\n".join(offenders)
