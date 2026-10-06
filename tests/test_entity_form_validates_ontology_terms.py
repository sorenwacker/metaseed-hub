"""Validate in an entity's form checks ontology terms.

Reported with a screenshot: an Organism field taking NCBITaxon terms held
``kjh`` and the form answered "Validation passed. No errors found." The form's
check built the entity and listed what construction refused; ontology terms are
deliberately not checked at construction, and nothing else asked. Dataset
validation did check them, so the two disagreed about the same value.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from metaseed.services.term_check import Outcome, TermVerdict
from metaseed.specs.schema import EntityDefSpec, FieldSpec, FieldType, ProfileSpec
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from metaseed_hub.auth import TokenUser
from metaseed_hub.ui.dependencies import tenant_slug_for
from metaseed_hub.ui.helpers import CSRF_TOKEN_COOKIE, get_or_create_csrf_token
from metaseed_hub.ui.routes import entity as entity_routes
from metaseed_hub.ui.spec_builder.state import SpecBuilderState
from tests.factories import make_dataset, make_spec_draft, make_tenant, make_user

pytestmark = pytest.mark.asyncio

SPEC = ProfileSpec(
    name="terms",
    version="0.1",
    root_entity="Init",
    entities={
        "Init": EntityDefSpec(
            description="a record",
            fields=[
                FieldSpec(
                    name="organism",
                    type=FieldType.ONTOLOGY_TERM,
                    required=True,
                    ontologies=["ncbitaxon"],
                )
            ],
        )
    },
)


async def _validate(session: AsyncSession, organism: str, verdict: TermVerdict | None) -> str:
    """Post the form's Validate for one value, with the term lookup answered by ``verdict``."""
    sub = "kc-terms"
    tenant = make_tenant(slug=tenant_slug_for(sub))
    session.add(tenant)
    await session.flush()
    user = make_user(tenant=tenant, keycloak_id=sub, email="t@example.org")
    session.add(user)
    await session.flush()
    draft = make_spec_draft(
        tenant=tenant, user=user, name="terms", spec_data=SpecBuilderState(spec=SPEC).to_dict()
    )
    session.add(draft)
    await session.flush()
    dataset = make_dataset(tenant=tenant, profile="terms", version="0.1")
    dataset.spec_draft_id = draft.id
    session.add(dataset)
    await session.commit()

    token = get_or_create_csrf_token(Mock(cookies={}))
    request = Mock()
    request.cookies = {CSRF_TOKEN_COOKIE: token}
    request.headers = {"X-CSRF-Token": token}

    async def form() -> FormData:
        return FormData({"_entity_type": "Init", "organism": organism})

    request.form = form
    seen: dict = {}

    def check(value, ontologies, source=None, within=None) -> TermVerdict:
        seen["asked"] = (value, tuple(ontologies or ()))
        assert verdict is not None
        return verdict

    with patch("metaseed.services.term_check.check_term", check):
        response = await entity_routes.dataset_entity_validate(
            request,
            dataset.id,
            session,
            TokenUser(sub=sub, email="t@example.org", name="T", roles=[]),
        )
    if verdict is not None:
        assert seen["asked"] == (organism, ("ncbitaxon",))
    return response.body.decode()


async def test_a_term_that_does_not_exist_is_an_issue(session: AsyncSession) -> None:
    html = await _validate(
        session,
        "NCBITaxon:99999999999",
        TermVerdict(Outcome.NOT_FOUND, "'NCBITaxon:99999999999' is not a term in ncbitaxon."),
    )
    assert "Validation passed" not in html
    assert "is not a term in ncbitaxon" in html
    assert "organism" in html


async def test_a_value_that_is_no_identifier_is_not_called_valid(session: AsyncSession) -> None:
    """The reported case: neither an error nor a pass, and said so."""
    html = await _validate(
        session,
        "kjh",
        TermVerdict(
            Outcome.NOT_CHECKED, "'kjh' is not an ontology identifier, so it cannot be checked."
        ),
    )
    assert "Validation passed" not in html
    assert "No errors found" in html
    assert "Not checked" in html
    assert "is not an ontology identifier" in html


async def test_an_outage_is_not_an_error(session: AsyncSession) -> None:
    html = await _validate(
        session,
        "NCBITaxon:3702",
        TermVerdict(Outcome.NOT_CHECKED, "The lookup service did not answer."),
    )
    assert "Validation issues" not in html
    assert "Not checked" in html


async def test_a_real_term_passes(session: AsyncSession) -> None:
    html = await _validate(session, "NCBITaxon:3702", TermVerdict(Outcome.OK))
    assert "Validation passed. No errors found." in html
    assert "Not checked" not in html
