"""The dataset timeline: editing sessions, named versions, the spec per version (261126).

The History tab listed every autosave as its own row, with no way to mark a
version that mattered and nothing recording which specification a version was
saved under. See docs/datasets/versions.md, *The timeline*.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from metaseed_hub.auth import TokenUser
from metaseed_hub.models import Dataset, DatasetVersion
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade, save_dataset_state
from metaseed_hub.ui.render import init_templates
from metaseed_hub.ui.routes.dataset.versions import get_dataset_versions, name_dataset_version
from metaseed_hub.ui.services.timeline import SESSION_GAP, editing_sessions
from tests.conftest import app_templates
from tests.factories import make_dataset, make_tenant, make_user

_CSRF = get_or_create_csrf_token(Mock(cookies={}))
_T0 = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _templates() -> None:
    """The routes render the way the running app does."""
    init_templates(app_templates())


def _version(number: int, author: str, minutes: float, name: str | None = None):
    return SimpleNamespace(
        version_number=number,
        created_by_id=author,
        created_at=_T0 + timedelta(minutes=minutes),
        name=name,
    )


class TestEditingSessions:
    def test_the_gap_is_thirty_minutes(self) -> None:
        assert timedelta(minutes=30) == SESSION_GAP

    def test_saves_by_one_person_close_together_are_one_session(self) -> None:
        versions = [_version(1, "ada", 0), _version(2, "ada", 10), _version(3, "ada", 35)]

        sessions = editing_sessions(versions)

        assert [[v.version_number for v in s.versions] for s in sessions] == [[3, 2, 1]]

    def test_a_long_gap_starts_a_new_session(self) -> None:
        versions = [_version(1, "ada", 0), _version(2, "ada", 31)]

        sessions = editing_sessions(versions)

        assert [[v.version_number for v in s.versions] for s in sessions] == [[2], [1]]

    def test_another_author_starts_a_new_session(self) -> None:
        versions = [_version(1, "ada", 0), _version(2, "bob", 1), _version(3, "ada", 2)]

        sessions = editing_sessions(versions)

        assert [[v.version_number for v in s.versions] for s in sessions] == [[3], [2], [1]]

    def test_a_session_knows_its_span_and_named_versions(self) -> None:
        versions = [
            _version(1, "ada", 0),
            _version(2, "ada", 5, name="Submitted"),
            _version(3, "ada", 9),
        ]

        (session,) = editing_sessions(versions)

        assert session.first.version_number == 1 and session.last.version_number == 3
        assert [v.version_number for v in session.named] == [2]


def _request(path: str, query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": query,
            "headers": [
                (b"cookie", f"{CSRF_TOKEN_COOKIE}={_CSRF}".encode()),
                (b"x-csrf-token", _CSRF.encode()),
            ],
        }
    )


async def _dataset_saved(session: AsyncSession, saves: int) -> tuple[Dataset, TokenUser]:
    sub = "kc-timeline"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id=sub, email="timeline@example.org"))
    dataset = make_dataset(tenant=tenant, profile="miappe", version="1.1")
    session.add(dataset)
    await session.commit()
    token = TokenUser(sub=sub, email="timeline@example.org", name="Timeline", roles=[])
    state = await ensure_dataset_facade(dataset, session)
    node = state.add_node("Investigation", {"unique_id": "I1", "title": "save 1"})
    await save_dataset_state(session, dataset, state, token)
    for n in range(2, saves + 1):
        state = await ensure_dataset_facade(dataset, session)
        state.update_node(node.id, {"unique_id": "I1", "title": f"save {n}"})
        await save_dataset_state(session, dataset, state, token)
    return dataset, token


async def _versions(session: AsyncSession, dataset_id: str) -> list[DatasetVersion]:
    rows = await session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_number)
    )
    return list(rows.scalars().all())


@pytest.mark.asyncio
class TestTheSpecOfAVersion:
    async def test_a_save_records_the_bound_specification(self, session: AsyncSession) -> None:
        dataset, _ = await _dataset_saved(session, 1)

        (version,) = await _versions(session, dataset.id)

        assert (version.spec_name, version.spec_version) == ("miappe", "1.1")


@pytest.mark.asyncio
class TestNamedVersions:
    async def test_naming_a_version_stores_its_name_and_note(self, session: AsyncSession) -> None:
        dataset, token = await _dataset_saved(session, 2)
        first = (await _versions(session, dataset.id))[0]

        response = await name_dataset_version(
            request=_request(f"/hub/datasets/{dataset.id}/versions/{first.id}/name"),
            dataset_id=dataset.id,
            version_id=first.id,
            session=session,
            user=token,
            name="  Submitted to ENA ",
            note="PRJEB0000",
        )

        assert response.status_code == 200
        await session.refresh(first)
        assert (first.name, first.note) == ("Submitted to ENA", "PRJEB0000")
        assert "Submitted to ENA" in response.body.decode()

    async def test_an_empty_name_removes_it(self, session: AsyncSession) -> None:
        dataset, token = await _dataset_saved(session, 1)
        (version,) = await _versions(session, dataset.id)
        version.name, version.note = "Old", "old note"
        await session.commit()

        await name_dataset_version(
            request=_request(f"/hub/datasets/{dataset.id}/versions/{version.id}/name"),
            dataset_id=dataset.id,
            version_id=version.id,
            session=session,
            user=token,
            name="",
            note="",
        )

        await session.refresh(version)
        assert (version.name, version.note) == (None, None)

    async def test_naming_changes_nothing_else(self, session: AsyncSession) -> None:
        dataset, token = await _dataset_saved(session, 2)
        before = [(v.version_number, v.data) for v in await _versions(session, dataset.id)]

        await name_dataset_version(
            request=_request(f"/hub/datasets/{dataset.id}/versions/x/name"),
            dataset_id=dataset.id,
            version_id=(await _versions(session, dataset.id))[0].id,
            session=session,
            user=token,
            name="Checkpoint",
            note="",
        )

        assert [(v.version_number, v.data) for v in await _versions(session, dataset.id)] == before


@pytest.mark.asyncio
class TestTheTimelineAsShown:
    async def test_saves_close_together_show_as_one_session(self, session: AsyncSession) -> None:
        dataset, token = await _dataset_saved(session, 3)

        html = (
            await get_dataset_versions(
                request=_request(f"/hub/datasets/{dataset.id}/versions"),
                dataset_id=dataset.id,
                session=session,
                user=token,
                named=False,
            )
        ).body.decode()

        assert html.count('data-testid="timeline-session"') == 1
        assert "3 saves" in html

    async def test_named_only_shows_the_named_versions(self, session: AsyncSession) -> None:
        dataset, token = await _dataset_saved(session, 3)
        versions = await _versions(session, dataset.id)
        versions[1].name = "Submitted to ENA"
        await session.commit()

        html = (
            await get_dataset_versions(
                request=_request(f"/hub/datasets/{dataset.id}/versions", b"named=1"),
                dataset_id=dataset.id,
                session=session,
                user=token,
                named=True,
            )
        ).body.decode()

        assert "Submitted to ENA" in html
        assert 'data-testid="timeline-session"' not in html
        assert html.count('data-testid="timeline-version"') == 1
