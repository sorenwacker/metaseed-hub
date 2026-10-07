"""An ontology field says how to search, and what its value means.

The search window opened on the Tab key and on nothing else: no button, no
hint, and the documentation described suggestions while typing, which the
dataset form does not have. A chosen term was stored and shown as its
identifier alone, so ``NCBITaxon:3702`` on a form said nothing about being
Arabidopsis thaliana. The endpoint that could say so read ``definition`` and
``id`` off a term that carries ``description`` and ``term_id``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from metaseed_hub.ui.routes import ontology_api

FORM = Path("src/metaseed_hub/ui/templates/partials/entity_form.html").read_text()
HUB_JS = Path("src/metaseed_hub/ui/static/js/hub.js").read_text()


def _ontology_blocks() -> list[str]:
    """The markup of each ontology field, up to the plain text input that follows it."""
    plain_input = '<input class="form-input" type="text"'
    return [block.split(plain_input)[0] for block in FORM.split('data-lookup-type="ontology"')[1:]]


def test_both_ontology_inputs_offer_a_search_button() -> None:
    blocks = _ontology_blocks()
    assert len(blocks) == 2, "the required and the optional section each render the field"
    for block in blocks:
        assert "data-ontology-search" in block
        assert ">Search</button>" in block


def test_the_form_says_that_tab_opens_the_search() -> None:
    for block in _ontology_blocks():
        assert "Tab" in block, "the key that opens the search is named beside the field"


def test_both_ontology_inputs_have_a_place_for_what_the_term_means() -> None:
    for block in _ontology_blocks():
        assert "data-term-info-for" in block


def test_the_button_opens_the_librarys_search_window() -> None:
    """The window is metaseed's; the hub calls it and defines none of it."""
    assert "openOntologyModal(" in HUB_JS
    assert "function openOntologyModal" not in HUB_JS


def test_the_term_is_asked_for_at_the_hubs_address() -> None:
    assert "/hub/api/ontology/term/" in HUB_JS


@pytest.mark.asyncio
async def test_the_term_endpoint_passes_on_name_definition_and_synonyms() -> None:
    term = SimpleNamespace(
        term_id="PO:0025034",
        label="leaf",
        description="A phyllome that is not associated with a reproductive structure.",
        ontology="PO",
        iri="http://purl.obolibrary.org/obo/PO_0025034",
        synonyms=["hoja"],
    )
    source = SimpleNamespace(get_term=AsyncMock(return_value=term))
    with patch.object(ontology_api, "get_term_source", return_value=source):
        response = await ontology_api.get_ontology_term("PO:0025034")

    import json

    body = json.loads(response.body)
    assert body["id"] == "PO:0025034"
    assert body["label"] == "leaf"
    assert body["definition"].startswith("A phyllome")
    assert body["synonyms"] == ["hoja"]


@pytest.mark.asyncio
async def test_a_term_without_a_definition_reports_none() -> None:
    """NCBITaxon terms have names and no definition; an empty or the literal
    string "None" must not be shown as one."""
    term = SimpleNamespace(
        term_id="NCBITaxon:3702",
        label="Arabidopsis thaliana",
        description=None,
        ontology="NCBITaxon",
        synonyms=["thale cress"],
    )
    source = SimpleNamespace(get_term=AsyncMock(return_value=term))
    with patch.object(ontology_api, "get_term_source", return_value=source):
        response = await ontology_api.get_ontology_term("NCBITaxon:3702")

    import json

    body = json.loads(response.body)
    assert body["label"] == "Arabidopsis thaliana"
    assert "definition" not in body
