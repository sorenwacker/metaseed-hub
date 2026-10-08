"""Datasets from repository records: the service, the background job and the tab.

A dataset could be filled from ENA, PRIDE, MetaboLights or a BrAPI server only
after it had been created, given a profile and named by hand. A route that
created one from an accession existed and no page posted to it. The repository
tab of the New Dataset screen takes the identifiers first: one dataset each,
named by the record's title.
See docs/datasets/import-export.md, *From a public repository*.
"""

from __future__ import annotations

import json
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
from metaseed_hub.models import Dataset, DatasetVersion, ImportJob, Notification
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE
from metaseed_hub.ui.services.repository_import import (
    MAX_IDENTIFIERS,
    import_record,
    mark_interrupted,
    parse_identifiers,
    record_title,
    repositories,
    run_job,
    start_job,
    summary,
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


async def _get(app, path: str) -> httpx.Response:
    with _signed_in():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            return await client.get(path)


async def _post(app, path: str, data: dict[str, str]) -> httpx.Response:
    with _signed_in():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            page = await client.get("/hub/datasets/new")
            csrf = page.cookies[CSRF_TOKEN_COOKIE]
            return await client.post(
                path,
                data=data,
                cookies=page.cookies,
                headers={"X-CSRF-Token": csrf, "Origin": "https://test"},
            )


async def _db_user_id(session: AsyncSession) -> str:
    from metaseed_hub.models import User

    return (
        await session.execute(select(User.id).where(User.keycloak_id == "kc-import"))
    ).scalar_one()


@pytest.mark.asyncio
class TestTheJob:
    """The submission as a whole: what runs after the request has answered."""

    async def test_it_works_through_the_list_and_tells_the_person_when_over(
        self, session, tenant
    ) -> None:
        from metaseed_hub.database import db

        await db.connect(_test_database_url())
        try:
            job = await start_job(
                session,
                tenant_id=tenant.id,
                user_id=await _db_user_id(session),
                profile="metabolights",
                identifiers=["MTBLS1", "MTBLS2"],
            )
            await session.commit()
            job_id = job.id

            with patch(IMPORTER, _importer):
                await run_job(job_id, _token(), lambda: db.session_factory())
        finally:
            await db.disconnect()

        session.expire_all()
        job = await session.get(ImportJob, job_id)
        assert job.status == "done" and job.finished_at is not None
        assert [o["status"] for o in job.outcomes] == ["imported", "imported"]
        assert await _names(session) == [
            "Tomato leaf metabolome",
            "Tomato leaf metabolome (MTBLS2)",
        ]
        note = (await session.execute(select(Notification))).scalars().one()
        assert (note.kind, note.actor_id, note.resource_id) == ("import_finished", None, job_id)
        assert (note.resource_title, note.detail) == ("2 metabolights records", "2 imported")

    async def test_a_failure_the_importer_did_not_foresee_is_an_outcome(
        self, session, tenant
    ) -> None:
        """Never a job left running: the person would wait for ever."""
        from metaseed_hub.database import db

        await db.connect(_test_database_url())
        try:
            job = await start_job(
                session,
                tenant_id=tenant.id,
                user_id=await _db_user_id(session),
                profile="metabolights",
                identifiers=["MTBLS1"],
            )
            await session.commit()
            job_id = job.id
            with patch(
                "metaseed_hub.ui.services.repository_import.import_record",
                side_effect=RuntimeError("the facade could not be built"),
            ):
                await run_job(job_id, _token(), lambda: db.session_factory())
        finally:
            await db.disconnect()

        session.expire_all()
        job = await session.get(ImportJob, job_id)
        assert job.status == "done"
        assert job.outcomes[0]["status"] == "failed"
        assert "could not be built" in job.outcomes[0]["detail"]

    async def test_a_job_cut_off_by_a_restart_is_closed_at_startup(self, session, tenant) -> None:
        job = await start_job(
            session,
            tenant_id=tenant.id,
            user_id=await _db_user_id(session),
            profile="metabolights",
            identifiers=["MTBLS1", "MTBLS2", "MTBLS3"],
        )
        job.outcomes = [
            {
                "identifier": "MTBLS1",
                "status": "imported",
                "dataset_id": None,
                "name": "x",
                "detail": "",
            }
        ]
        await session.commit()

        job_id = job.id

        await mark_interrupted(session)
        await session.commit()

        session.expire_all()
        job = await session.get(ImportJob, job_id)
        assert job.status == "interrupted"
        assert [o["status"] for o in job.outcomes] == ["imported", "not_checked", "not_checked"]
        assert summary(job.outcomes) == "1 imported, 2 not checked"


@pytest.mark.asyncio
class TestThePage:
    async def test_every_importer_adds_its_button_to_the_tab(self, app_db, tenant) -> None:
        page = await _get(create_app(), "/hub/datasets/new")

        assert page.status_code == 200
        assert 'data-tab="repository"' in page.text
        for repository in repositories():
            assert f'name="profile" value="{repository.profile}"' in page.text, repository.profile
            assert f">{repository.button}</button>" in page.text

    async def test_a_submission_starts_a_job_and_answers_with_a_panel_that_polls(
        self, app_db, tenant, session
    ) -> None:
        app = create_app()

        with patch(IMPORTER, _importer):
            response = await _post(
                app,
                "/hub/import/jobs",
                {"profile": "metabolights", "identifiers": "MTBLS1\nMTBLS2"},
            )
            await app.state.import_jobs.wait()

        assert response.status_code == 200
        assert 'data-testid="import-progress" max="2" value="0"' in response.text
        assert response.text.count('data-status="waiting"') == 2
        job = (await session.execute(select(ImportJob))).scalars().one()
        assert f'hx-get="/hub/import/jobs/{job.id}" hx-trigger="every 2s"' in response.text
        assert json.loads(response.headers["HX-Trigger-After-Swap"])["importJobProgress"] == {
            "job": job.id,
            "status": "running",
            "done": 0,
            "total": 2,
            "imported": [],
            "summary": "",
        }
        # The request answered before the job ran; the job ran after it.
        assert job.status == "done"
        assert await _names(session) == [
            "Tomato leaf metabolome",
            "Tomato leaf metabolome (MTBLS2)",
        ]

    async def test_a_finished_job_s_panel_links_its_datasets_and_stops_polling(
        self, app_db, tenant, session
    ) -> None:
        app = create_app()
        with patch(IMPORTER, _importer):
            await _post(
                app, "/hub/import/jobs", {"profile": "metabolights", "identifiers": "MTBLS1"}
            )
            await app.state.import_jobs.wait()
        job = (await session.execute(select(ImportJob))).scalars().one()
        dataset = (await session.execute(select(Dataset))).scalars().one()

        panel = await _get(app, f"/hub/import/jobs/{job.id}")

        assert panel.status_code == 200
        assert "hx-trigger" not in panel.text
        assert 'data-testid="import-progress" max="1" value="1"' in panel.text
        assert f'href="/hub/datasets/{dataset.id}"' in panel.text
        told = json.loads(panel.headers["HX-Trigger-After-Swap"])["importJobProgress"]
        assert told["status"] == "done"
        assert told["imported"] == [{"id": dataset.id, "name": dataset.name}]
        assert told["summary"] == "1 imported"

    async def test_another_person_s_job_is_not_shown(self, app_db, tenant, session) -> None:
        from tests.factories import make_user

        other = make_user(tenant=tenant, keycloak_id="kc-other", email="other@example.org")
        session.add(other)
        await session.flush()
        job = await start_job(
            session, tenant_id=tenant.id, user_id=other.id, profile="ena", identifiers=["PRJEB1"]
        )
        await session.commit()

        assert (await _get(create_app(), f"/hub/import/jobs/{job.id}")).status_code == 404

    async def test_more_than_one_submission_takes_starts_nothing(
        self, app_db, tenant, session
    ) -> None:
        too_many = "\n".join(f"MTBLS{n}" for n in range(MAX_IDENTIFIERS + 1))

        response = await _post(
            create_app(), "/hub/import/jobs", {"profile": "metabolights", "identifiers": too_many}
        )

        assert 'data-testid="import-problem"' in response.text
        assert (await session.execute(select(ImportJob))).scalars().all() == []

    async def test_the_tab_lists_the_person_s_recent_jobs(self, app_db, tenant, session) -> None:
        job = await start_job(
            session,
            tenant_id=tenant.id,
            user_id=await _db_user_id(session),
            profile="ena",
            identifiers=["PRJEB1"],
        )
        job.status = "interrupted"
        job.outcomes = [
            {
                "identifier": "PRJEB1",
                "status": "not_checked",
                "dataset_id": None,
                "name": "",
                "detail": "The hub restarted before this identifier was reached.",
            }
        ]
        await session.commit()

        page = await _get(create_app(), "/hub/datasets/new")

        assert f'id="import-job-{job.id}"' in page.text
        assert "interrupted by a hub restart" in page.text


def test_the_page_script_announces_imports_and_opens_the_tab_a_link_names() -> None:
    script = (TEMPLATES.parent / "static" / "js" / "hub.js").read_text()

    assert "addEventListener('importJobProgress'" in script
    assert "showToast('Imported '" in script
    assert "'.source-tab[data-tab=\"' + wanted + '\"]'" in script


def test_the_header_has_no_import_entry_of_its_own() -> None:
    """Importing is a way of creating a dataset, so it lives on that screen."""
    header = (TEMPLATES / "base.html").read_text()

    assert "/hub/import" not in header
