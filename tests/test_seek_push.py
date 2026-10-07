"""Pushing a dataset to SEEK: hub-stored specifications, the check that comes
first, the wait, the confirmation and the project choice.

Split from ``test_seek_plugin.py``, which holds the connection, the page and
the panel; the fixtures and request helpers are that module's.
"""

# The fixtures are imported by name and then named as parameters, which is how
# pytest finds them and what F811 flags.
# ruff: noqa: F811
from __future__ import annotations

import re

from tests.test_seek_plugin import (  # noqa: F401
    HUB_SPEC,
    SEEK_PAGE,
    _get,
    _installed_templates,
    _post,
    _save_settings,
    _working,
    app_db,
    dataset,
    draft_dataset,
    published_dataset,
)


class TestADatasetOnAHubStoredSpecification:
    """The SEEK routes looked the profile up among the installed ones, by name.
    A dataset bound to a draft or a published specification has its profile in
    the database, so the lookup failed, read as "does not map onto SEEK", and
    hid the whole panel from exactly the specifications written for SEEK."""

    async def test_the_panel_appears_for_a_draft(self, draft_dataset, app_db) -> None:
        html = (await _get(f"/hub/datasets/{draft_dataset.id}")).text
        assert 'data-testid="seek-panel"' in html

    async def test_the_panel_appears_for_a_published_specification(
        self, published_dataset, app_db
    ) -> None:
        html = (await _get(f"/hub/datasets/{published_dataset.id}")).text
        assert 'data-testid="seek-panel"' in html

    async def test_a_draft_yields_its_templates(self, draft_dataset, app_db) -> None:
        response = await _get(f"/hub/seek/datasets/{draft_dataset.id}/templates")
        assert response.status_code == 200
        names = [t["metadata"]["name"] for t in response.json()["data"]]
        assert names and all(HUB_SPEC in name for name in names)

    async def test_a_published_specification_yields_its_templates(
        self, published_dataset, app_db
    ) -> None:
        response = await _get(f"/hub/seek/datasets/{published_dataset.id}/templates")
        assert response.status_code == 200
        assert response.json()["data"]

    async def test_the_check_reads_its_templates(self, draft_dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)

        def nothing_installed(factory) -> None:
            factory.return_value.template_ids_by_title.return_value = {}

        response = await _post(f"/hub/seek/datasets/{draft_dataset.id}/check", nothing_installed)
        assert "does not map onto SEEK" not in response.text
        assert "not installed" in response.text
        assert HUB_SPEC in response.text

    async def test_the_push_provisions_its_specification(self, draft_dataset, app_db) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        await _save_settings("https://seek.example.org", _working)
        plan = MagicMock()
        pushed = SimpleNamespace(
            investigations=["1"], studies=[], assays=[], samples=[], errors=[], unlinked=[]
        )

        response = await _post(
            f"/hub/seek/datasets/{draft_dataset.id}/push",
            metaseed__seek__build_provisioning_plan=plan,
            metaseed__seek__execute_provisioning_plan=MagicMock(),
            metaseed__seek__provision__resolve_cv_ids=MagicMock(return_value={}),
            metaseed__seek__sync_dataset_to_seek=MagicMock(return_value=pushed),
        )

        assert 'data-testid="seek-result-ok"' in response.text, response.text
        assert plan.call_args.args[0].name == HUB_SPEC


class TestAPushThatTakesLong:
    """A push to a small SEEK reported "timed out" with one Investigation
    created and nothing else. SEEK had answered ``POST /isa_assays`` after 35
    seconds; the client had given up at 30. The request succeeded and the hub
    called it an error, with no word that pushing again would continue."""

    async def test_the_push_waits_longer_than_seek_takes_to_build_an_assay(
        self, dataset, app_db
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        await _save_settings("https://seek.example.org", _working)
        pushed = SimpleNamespace(
            investigations=["1"], studies=[], assays=[], samples=[], errors=[], unlinked=[]
        )
        seen: dict = {}

        def remember(factory) -> None:
            seen["factory"] = factory

        await _post(
            f"/hub/seek/datasets/{dataset.id}/push",
            remember,
            metaseed__seek__build_provisioning_plan=MagicMock(),
            metaseed__seek__execute_provisioning_plan=MagicMock(),
            metaseed__seek__provision__resolve_cv_ids=MagicMock(return_value={}),
            metaseed__seek__sync_dataset_to_seek=MagicMock(return_value=pushed),
        )

        assert seen["factory"].call_args.kwargs["timeout"] >= 180

    def test_an_error_says_pushing_again_continues(self) -> None:
        from types import SimpleNamespace

        from metaseed_hub.ui.render import get_templates

        stopped = SimpleNamespace(
            investigations=["1"],
            studies=[],
            assays=[],
            samples=[],
            errors=[("node", "timed out")],
            unlinked=[],
        )
        html = (
            get_templates()
            .get_template("partials/seek_panel_result.html")
            .render(result=stopped, message=None, error=None)
        )
        assert 'data-testid="seek-result-resume"' in html
        assert "Push again" in html

    async def test_the_push_asks_to_confirm_the_project(self, dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(SEEK_PAGE)).text
        row = html.split(f'data-testid="seek-dataset-{dataset.id}"')[1].split("</li>")[0]
        confirm = row.split('hx-confirm="')[1].split('"')[0]
        # The instance is read out of the sentence and compared whole: a
        # substring test on a host name would also pass for a look-alike.
        project, instance = re.search(r"to project (.+) on (\S+)\?", confirm).groups()
        assert (project, instance) == ("Tulip", "https://seek.example.org")
        assert dataset.name in confirm

    async def test_nothing_is_confirmed_without_a_connection(self, dataset, app_db) -> None:
        html = (await _get(SEEK_PAGE)).text
        assert "hx-confirm" not in html.split(f'data-testid="seek-dataset-{dataset.id}"')[1]


class TestTheCheckLooksForTheTemplatesTheFileHolds:
    """Check SEEK looked for three titles built from the profile's name. The
    file the hub generates names its assay template "... assay - data file",
    and a template-bound profile's templates carry the titles its entities
    name, so the check reported installed templates as missing -- every one of
    them for the CropXR profiles."""

    @staticmethod
    def _titles() -> list[str]:
        from metaseed.seek.templates import to_isa_template_json
        from metaseed.specs.loader import SpecLoader

        document = to_isa_template_json(SpecLoader().load_profile("3.0", "seek-ready-template"))
        return [template["metadata"]["name"] for template in document["data"]]

    async def test_every_generated_template_installed_reads_as_ready(self, dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)
        titles = self._titles()

        def installed(factory) -> None:
            factory.return_value.template_ids_by_title.return_value = {
                title: str(index) for index, title in enumerate(titles)
            }

        response = await _post(f"/hub/seek/datasets/{dataset.id}/check", installed)
        assert "Ready" in response.text, response.text

    async def test_a_missing_generated_template_is_named(self, dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)
        titles = self._titles()

        def all_but_the_last(factory) -> None:
            factory.return_value.template_ids_by_title.return_value = dict.fromkeys(
                titles[:-1], "1"
            )

        response = await _post(f"/hub/seek/datasets/{dataset.id}/check", all_but_the_last)
        assert titles[-1] in response.text
        assert "1 ISA Template(s) are not installed" in response.text


class TestAPushChecksFirst:
    """A push to a SEEK without the templates created the Investigation and the
    Studies, then failed once per Study and sample table. The check that would
    have said so was a separate button."""

    async def test_nothing_is_sent_when_a_template_is_missing(self, dataset, app_db) -> None:
        from unittest.mock import MagicMock

        await _save_settings("https://seek.example.org", _working)
        provision, sync = MagicMock(), MagicMock()

        def nothing_installed(factory) -> None:
            factory.return_value.template_ids_by_title.return_value = {}

        response = await _post(
            f"/hub/seek/datasets/{dataset.id}/push",
            nothing_installed,
            metaseed__seek__build_provisioning_plan=MagicMock(),
            metaseed__seek__execute_provisioning_plan=provision,
            metaseed__seek__provision__resolve_cv_ids=MagicMock(return_value={}),
            metaseed__seek__sync_dataset_to_seek=sync,
        )

        assert "not installed" in response.text
        assert "Nothing was sent" in response.text
        provision.assert_not_called()
        sync.assert_not_called()

    async def test_each_missing_template_is_named_once(self, dataset, app_db) -> None:
        await _save_settings("https://seek.example.org", _working)

        def nothing_installed(factory) -> None:
            factory.return_value.template_ids_by_title.return_value = {}

        response = await _post(f"/hub/seek/datasets/{dataset.id}/push", nothing_installed)
        assert response.text.count("seek-ready-template study source") == 1


class TestTheProjectIsSavedWhenChosen:
    async def test_picking_a_project_submits_the_form(self, dataset, app_db) -> None:
        """Changing the select alone did nothing until a second button was
        pressed, and a push then went to the project shown before."""
        await _save_settings("https://seek.example.org", _working)
        html = (await _get(SEEK_PAGE)).text
        select = html.split('data-testid="seek-project"')[0].rsplit("<select", 1)[1]
        assert "data-submit-on-change" in select
