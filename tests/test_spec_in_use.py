"""A published specification cannot be withdrawn while datasets are built on it.

acdc_ks 2.0 was withdrawn on 260728. Two datasets in another account were
built on it, and from that moment every page of theirs raised SpecLoadError.
Nothing warned, because withdrawal only ever looked at the withdrawer's own
account — and datasets bind to a specification by name and version, not by
foreign key, so even a same-account check by id would have found nothing.
"""

from __future__ import annotations

import pytest
from metaseed.specs.schema import ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec, SpecStatus
from metaseed_hub.ui.spec_builder.access import (
    SpecInUseError,
    datasets_using_spec,
    unpublish_spec,
)
from tests.factories import make_dataset, make_spec, make_tenant, make_user

SPEC_DATA = {"spec": ProfileSpec(name="acdc_ks", version="2.0").model_dump(mode="json")}


async def _published_spec(session: AsyncSession, tenant, user) -> Spec:
    spec = make_spec(
        tenant=tenant,
        created_by=user,
        name="acdc_ks",
        version="2.0",
        spec_data=SPEC_DATA,
        status=SpecStatus.PUBLISHED,
    )
    session.add(spec)
    await session.commit()
    return spec


@pytest.fixture
async def publisher(session: AsyncSession):
    tenant = make_tenant(slug="publisher")
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-pub", email="pub@example.org")
    session.add(user)
    await session.commit()
    return tenant, user


@pytest.fixture
async def someone_else(session: AsyncSession):
    tenant = make_tenant(slug="other")
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id="kc-other", email="other@example.org")
    session.add(user)
    await session.commit()
    return tenant, user


class TestFindingDatasetsAtRisk:
    async def test_a_dataset_in_another_account_counts(
        self, session, publisher, someone_else
    ) -> None:
        """The datasets that break are usually not the publisher's own."""
        spec = await _published_spec(session, *publisher)
        other_tenant, _ = someone_else
        session.add(
            make_dataset(tenant=other_tenant, name="Test_WES", profile="acdc_ks", version="2.0")
        )
        await session.commit()

        assert await datasets_using_spec(session, spec) == ["Test_WES"]

    async def test_a_dataset_on_another_version_does_not(
        self, session, publisher, someone_else
    ) -> None:
        spec = await _published_spec(session, *publisher)
        other_tenant, _ = someone_else
        session.add(make_dataset(tenant=other_tenant, name="old", profile="acdc_ks", version="1.0"))
        await session.commit()

        assert await datasets_using_spec(session, spec) == []

    async def test_a_deleted_dataset_does_not_hold_it_hostage(
        self, session, publisher, someone_else
    ) -> None:
        spec = await _published_spec(session, *publisher)
        other_tenant, _ = someone_else
        dataset = make_dataset(tenant=other_tenant, name="gone", profile="acdc_ks", version="2.0")
        session.add(dataset)
        await session.flush()
        dataset.soft_delete()
        await session.commit()

        assert await datasets_using_spec(session, spec) == []


class TestWithdrawing:
    async def test_it_refuses_while_a_dataset_needs_it(
        self, session, publisher, someone_else
    ) -> None:
        spec = await _published_spec(session, *publisher)
        _, publishing_user = publisher
        other_tenant, _ = someone_else
        session.add(
            make_dataset(tenant=other_tenant, name="Test_WES", profile="acdc_ks", version="2.0")
        )
        await session.commit()

        with pytest.raises(SpecInUseError) as raised:
            await unpublish_spec(session, spec, publishing_user.id)

        assert "Test_WES" in str(raised.value)
        assert spec.deleted_at is None, "the spec must still be there"

    async def test_it_still_works_when_nothing_uses_it(self, session, publisher) -> None:
        spec = await _published_spec(session, *publisher)
        _, publishing_user = publisher

        draft = await unpublish_spec(session, spec, publishing_user.id)

        assert draft.name == "acdc_ks"
        assert spec.deleted_at is not None


class TestThePageSaysSoBeforeTheClick:
    """A refusal after the fact is not enough — the page has to say a
    specification is load-bearing while the button is still unpressed."""

    async def test_the_view_page_names_the_datasets_and_blocks_the_button(
        self, session, publisher, someone_else
    ) -> None:
        from tests.test_spec_versioning import _endpoint, _request

        spec = await _published_spec(session, *publisher)
        publisher_tenant, publishing_user = publisher
        other_tenant, _ = someone_else
        session.add(
            make_dataset(tenant=other_tenant, name="Test_WES", profile="acdc_ks", version="2.0")
        )
        await session.commit()

        view = _endpoint("/spec/{spec_id}", "GET")
        response = await view(
            _request("GET"), spec.id, session, (publishing_user.id, publisher_tenant.id)
        )
        html = response.body.decode()

        assert 'data-testid="spec-in-use"' in html
        assert "Test_WES" in html
        assert 'data-testid="unpublish-blocked"' in html


class TestTheButtonIsAnElement:
    """The button rendered as text once: another block had been pasted into
    the middle of its tag, so ``title="..."`` and ``Unpublish`` appeared on
    the page as words and nothing was clickable. A substring check on
    ``data-testid`` cannot tell the two apart; parsing the HTML can."""

    async def test_unpublish_is_a_real_button_inside_the_form(self, session, publisher) -> None:
        from html.parser import HTMLParser

        from tests.test_spec_versioning import _endpoint, _request

        spec = await _published_spec(session, *publisher)
        publisher_tenant, publishing_user = publisher
        view = _endpoint("/spec/{spec_id}", "GET")
        response = await view(
            _request("GET"), spec.id, session, (publishing_user.id, publisher_tenant.id)
        )
        html = response.body.decode()

        class _Buttons(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.buttons: list[dict[str, str | None]] = []
                self.text: list[str] = []
                self._in_button: dict[str, str | None] | None = None

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag == "button":
                    self._in_button = dict(attrs)
                    self.buttons.append(self._in_button)

            def handle_endtag(self, tag: str) -> None:
                if tag == "button":
                    self._in_button = None

            def handle_data(self, data: str) -> None:
                if self._in_button is not None:
                    self._in_button["_text"] = (self._in_button.get("_text") or "") + data
                else:
                    self.text.append(data)

        parser = _Buttons()
        parser.feed(html)
        unpublish = [b for b in parser.buttons if (b.get("_text") or "").strip() == "Unpublish"]
        assert unpublish, "no <button> whose text is Unpublish"
        assert unpublish[0].get("data-testid") == "unpublish"
        assert unpublish[0].get("type") == "submit"
        assert "Withdraw from the organisation" in (unpublish[0].get("title") or "")
        stray = "".join(parser.text)
        assert "title=" not in stray and ">Unpublish" not in stray, (
            "button markup is leaking onto the page as text"
        )
