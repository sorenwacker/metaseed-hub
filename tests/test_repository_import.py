"""Datasets from repository records: the service and the repository tab.

A dataset could be filled from ENA, PRIDE, MetaboLights or a BrAPI server only
after it had been created, given a profile and named by hand. A route that
created one from an accession existed and no page posted to it. The repository
tab of the New Dataset screen takes the identifiers first: one dataset each,
named by the record's title.
See docs/datasets/import-export.md, *From a public repository*.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from metaseed import MetaseedClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.auth import TokenUser
from metaseed_hub.main import create_app
from metaseed_hub.models import Dataset, DatasetVersion
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE
from metaseed_hub.ui.services.repository_import import (
    MAX_IDENTIFIERS,
    import_record,
    parse_identifiers,
    record_title,
    repositories,
)
from tests.conftest import _test_database_url
from tests.factories import make_dataset, make_tenant, make_user

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "metaseed_hub" / "ui" / "templates"
IMPORTER = "metaseed.metabolights.import_accession"


def _study(accession: str, title: str | None = "Tomato leaf metabolome") -> MetaseedClient:
    client = MetaseedClient("metabolights", "1.0")
    data = {"identifier": accession, "description": "d"}
    if title:
        data["title"] = title
    client.create_entity("Investigation", data, skip_validation=True)
    return client


def _importer(accession: str, **_kw: object) -> MetaseedClient:
    return _study(accession)


def _nothing(_accession: str, **_kw: object) -> MetaseedClient:
    return MetaseedClient("metabolights", "1.0")


def _token() -> TokenUser:
    return TokenUser(sub="kc-import", email="importer@example.org", name="I", roles=[])


@pytest.fixture
async def tenant(session: AsyncSession):
    from metaseed_hub.ui.dependencies import tenant_slug_for

    tenant = make_tenant(slug=tenant_slug_for("kc-import"))
    session.add(tenant)
    await session.flush()
    session.add(make_user(tenant=tenant, keycloak_id="kc-import", email="importer@example.org"))
    await session.commit()
    return tenant


async def _names(session: AsyncSession) -> list[str]:
    return sorted((await session.execute(select(Dataset.name))).scalars().all())


class TestWhatIsAskedFor:
    def test_the_repositories_come_from_the_registry(self) -> None:
        assert {"ena", "pride", "metabolights", "miappe"} <= {r.profile for r in repositories()}

    def test_identifiers_are_one_per_line_without_blanks_or_repeats(self) -> None:
        assert parse_identifiers(" MTBLS1 \n\nMTBLS2\r\nMTBLS1\n") == ["MTBLS1", "MTBLS2"]


class TestTheNameComesFromTheRecord:
    def test_the_root_record_s_title_names_the_dataset(self) -> None:
        assert record_title(_study("MTBLS1"), "MTBLS1") == "Tomato leaf metabolome"

    def test_a_record_without_a_title_is_named_by_its_identifier(self) -> None:
        assert record_title(_study("MTBLS1", title=None), "MTBLS1") == "MTBLS1"


@pytest.mark.asyncio
class TestOneRecord:
    async def test_it_becomes_a_dataset_named_by_its_title(self, session, tenant) -> None:
        with patch(IMPORTER, _importer):
            outcome = await import_record(session, tenant.id, "metabolights", "MTBLS1", _token())

        assert outcome.status == "imported"
        assert outcome.dataset.name == "Tomato leaf metabolome"
        assert outcome.dataset.profile == "metabolights"
        assert "MTBLS1" in str(outcome.dataset.data)

    async def test_a_taken_name_gets_the_identifier_appended(self, session, tenant) -> None:
        session.add(make_dataset(tenant=tenant, name="Tomato leaf metabolome"))
        await session.commit()

        with patch(IMPORTER, _importer):
            outcome = await import_record(session, tenant.id, "metabolights", "MTBLS1", _token())

        assert outcome.status == "imported"
        assert outcome.dataset.name == "Tomato leaf metabolome (MTBLS1)"

    async def test_a_record_held_under_both_names_is_already_imported(
        self, session, tenant
    ) -> None:
        # Read before the refusals: a refused name rolls the session back.
        tenant_id = tenant.id
        with patch(IMPORTER, _importer):
            for _ in range(2):
                await import_record(session, tenant_id, "metabolights", "MTBLS1", _token())
                await session.commit()
            outcome = await import_record(session, tenant_id, "metabolights", "MTBLS1", _token())
            await session.commit()

        assert outcome.status == "duplicate"
        assert outcome.dataset is None
        assert await _names(session) == [
            "Tomato leaf metabolome",
            "Tomato leaf metabolome (MTBLS1)",
        ]

    async def test_a_record_holding_nothing_creates_nothing(self, session, tenant) -> None:
        with patch(IMPORTER, _nothing):
            outcome = await import_record(session, tenant.id, "metabolights", "MTBLS404", _token())

        assert outcome.status == "empty"
        assert await _names(session) == []

    @pytest.mark.parametrize(
        "raised",
        [
            httpx.ConnectTimeout("no answer"),
            httpx.ReadTimeout("no answer"),
            httpx.HTTPStatusError(
                "down",
                request=httpx.Request("GET", "https://repository.example.org"),
                response=httpx.Response(503),
            ),
        ],
    )
    async def test_a_repository_that_does_not_answer_is_not_checked(
        self, session, tenant, raised
    ) -> None:
        def _down(_accession: str, **_kw: object) -> MetaseedClient:
            raise raised

        with patch(IMPORTER, _down):
            outcome = await import_record(session, tenant.id, "metabolights", "MTBLS1", _token())

        assert outcome.status == "not_checked"
        assert await _names(session) == []

    async def test_an_identifier_the_importer_rejects_has_failed(self, session, tenant) -> None:
        def _rejects(_accession: str, **_kw: object) -> MetaseedClient:
            raise ValueError("not a MetaboLights accession")

        with patch(IMPORTER, _rejects):
            outcome = await import_record(session, tenant.id, "metabolights", "nonsense", _token())

        assert outcome.status == "failed"
        assert "not a MetaboLights accession" in outcome.detail

    async def test_a_profile_without_an_importer_is_refused(self, session, tenant) -> None:
        with pytest.raises(LookupError):
            await import_record(session, tenant.id, "darwin-core", "X", _token())

    async def test_the_importer_runs_off_the_event_loop(self, session, tenant) -> None:
        loop_thread = threading.get_ident()
        seen: list[int] = []

        def _records_thread(accession: str, **_kw: object) -> MetaseedClient:
            seen.append(threading.get_ident())
            return _study(accession)

        with patch(IMPORTER, _records_thread):
            await import_record(session, tenant.id, "metabolights", "MTBLS1", _token())

        assert seen and seen[0] != loop_thread, "the importer ran on the event loop"

    async def test_the_first_version_is_authored_by_the_importer(self, session, tenant) -> None:
        with patch(IMPORTER, _importer):
            outcome = await import_record(session, tenant.id, "metabolights", "MTBLS1", _token())

        versions = (
            (
                await session.execute(
                    select(DatasetVersion).where(DatasetVersion.dataset_id == outcome.dataset.id)
                )
            )
            .scalars()
            .all()
        )
        assert versions and versions[0].created_by_id is not None


@pytest.fixture
async def app_db(session):
    from metaseed_hub.database import db

    await db.connect(_test_database_url())
    yield
    await db.disconnect()


def _signed_in():
    return patch(
        "metaseed_hub.ui.dependencies.get_current_user_from_cookie",
        AsyncMock(return_value=_token()),
    )


async def _post(path: str, data: dict[str, str]) -> httpx.Response:
    with _signed_in():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="https://test"
        ) as client:
            page = await client.get("/hub/datasets/new")
            csrf = page.cookies[CSRF_TOKEN_COOKIE]
            return await client.post(
                path,
                data=data,
                cookies=page.cookies,
                headers={"X-CSRF-Token": csrf, "Origin": "https://test"},
            )


@pytest.mark.asyncio
class TestThePage:
    async def test_every_importer_adds_its_button_to_the_tab(self, app_db, tenant) -> None:
        with _signed_in():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()), base_url="https://test"
            ) as client:
                page = await client.get("/hub/datasets/new")

        assert page.status_code == 200
        assert 'data-tab="repository"' in page.text
        for repository in repositories():
            assert f'name="profile" value="{repository.profile}"' in page.text, repository.profile
            assert f">{repository.button}</button>" in page.text

    async def test_each_identifier_gets_a_row_that_imports_in_turn(self, app_db, tenant) -> None:
        rows = await _post(
            "/hub/import/rows", {"profile": "metabolights", "identifiers": "MTBLS1\nMTBLS2"}
        )

        assert rows.status_code == 200
        assert rows.text.count('hx-post="/hub/import/record"') == 2
        assert rows.text.count('hx-sync="#import-queue:queue all"') == 2
        assert 'value="MTBLS1"' in rows.text and 'value="MTBLS2"' in rows.text

    async def test_more_than_one_submission_takes_imports_nothing(self, app_db, tenant) -> None:
        too_many = "\n".join(f"MTBLS{n}" for n in range(MAX_IDENTIFIERS + 1))

        rows = await _post("/hub/import/rows", {"profile": "metabolights", "identifiers": too_many})

        assert 'data-testid="import-problem"' in rows.text
        assert "hx-post" not in rows.text

    async def test_a_row_creates_its_dataset_and_links_it(self, app_db, tenant, session) -> None:
        with patch(IMPORTER, _importer):
            row = await _post(
                "/hub/import/record", {"profile": "metabolights", "identifier": "MTBLS1"}
            )

        assert row.status_code == 200
        assert 'data-status="imported"' in row.text
        dataset = (await session.execute(select(Dataset))).scalars().one()
        assert dataset.name == "Tomato leaf metabolome"
        assert f'href="/hub/datasets/{dataset.id}"' in row.text

    async def test_a_row_that_fails_still_answers_with_a_row(self, app_db, tenant, session) -> None:
        with patch(IMPORTER, _nothing):
            row = await _post(
                "/hub/import/record", {"profile": "metabolights", "identifier": "MTBLS404"}
            )

        assert row.status_code == 200
        assert 'data-status="empty"' in row.text
        assert await _names(session) == []


def test_the_header_has_no_import_entry_of_its_own() -> None:
    """Importing is a way of creating a dataset, so it lives on that screen."""
    header = (TEMPLATES / "base.html").read_text()

    assert "/hub/import" not in header
