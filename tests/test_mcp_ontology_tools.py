"""The ontology lookup tools exposed over MCP.

Lookups are mocked at the port the hub asks — metaseed's term router — so no
test reaches EMBL-EBI, and the tools are exercised the way they actually
resolve: through whichever sources are configured, of which OLS is one.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from metaseed.services.ontology import OntologyTerm
from metaseed.services.terms import TermHit
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.mcp import NotAuthenticatedError
from tests.mcp_helpers import _calling_with, _tool, _user_with_token


class _FakeRouter:
    """Stands in for the term router, so no test reaches a real source."""

    def __init__(
        self,
        results: list[TermHit] | None = None,
        term: OntologyTerm | None = None,
    ) -> None:
        self._results = results or []
        self._term = term
        self.calls: list[tuple] = []

    async def search(self, query, ontology=None, limit=20):
        self.calls.append(("search", query, ontology, limit))
        return list(self._results)

    async def get_term(self, term_id):
        self.calls.append(("get_term", term_id))
        return self._term


def _drought() -> TermHit:
    return TermHit(
        id="PECO:0007404",
        label="drought environment",
        description="An environment lacking water.",
        ontology="PECO",
        source="OntologyService",
    )


class TestOntologyTools:
    """Read-only OLS lookups through the service layer the web UI already uses.

    Authenticated but not dataset-scoped: a lookup touches nobody's data, yet
    an open endpoint would let an unauthenticated caller drive traffic through
    the hub (the same reasoning as ui/routes/ontology_api.py).
    """

    async def test_search_returns_results(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00001", email="ont00001@example.org"
        )
        fake = _FakeRouter(results=[_drought()])

        search = await _tool(server, "search_ontology")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_term_source", return_value=fake),
            _calling_with(secret),
        ):
            result = json.loads(await search("drought", ontology="peco", rows=5))

        assert result["total_found"] == 1
        assert result["results"][0]["id"] == "PECO:0007404"
        assert result["results"][0]["label"] == "drought environment"
        assert result["results"][0]["description"] == "An environment lacking water."
        assert ("search", "drought", "peco", 5) in fake.calls

    async def test_a_lookup_still_requires_a_token(self, server, session: AsyncSession) -> None:
        """Authentication-only is not authentication-optional."""
        fake = _FakeRouter(results=[_drought()])

        search = await _tool(server, "search_ontology")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_term_source", return_value=fake),
            _calling_with(None),
            pytest.raises(NotAuthenticatedError),
        ):
            await search("drought")

    async def test_a_term_can_be_read_in_detail(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00002", email="ont00002@example.org"
        )
        fake = _FakeRouter(
            term=OntologyTerm(
                term_id="PATO:0000015",
                label="color",
                description="A composite chromatic quality.",
                ontology="PATO",
                iri="http://purl.obolibrary.org/obo/PATO_0000015",
                synonyms=["colour"],
            )
        )

        get_term = await _tool(server, "get_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_term_source", return_value=fake),
            _calling_with(secret),
        ):
            result = json.loads(await get_term("PATO:0000015"))

        assert result["id"] == "PATO:0000015"
        assert result["label"] == "color"
        assert result["definition"] == "A composite chromatic quality."
        assert result["synonyms"] == ["colour"]

    async def test_a_missing_term_is_an_error_the_agent_can_act_on(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00003", email="ont00003@example.org"
        )

        class _CarriedRouter(_FakeRouter):
            def has_ontology_sync(self, ontology_id: str):
                # The source carries the ontology and looked: absence is real.
                return True

        fake = _CarriedRouter(term=None)

        get_term = await _tool(server, "get_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_term_source", return_value=fake),
            _calling_with(secret),
            pytest.raises(ValueError, match="No ontology term"),
        ):
            await get_term("PATO:9999999")

    async def test_suggestions_are_light(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00004", email="ont00004@example.org"
        )
        fake = _FakeRouter(results=[_drought()])

        suggest = await _tool(server, "suggest_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_term_source", return_value=fake),
            _calling_with(secret),
        ):
            result = json.loads(await suggest("drou"))

        assert result["suggestions"] == [
            {
                "id": "PECO:0007404",
                "label": "drought environment",
                "ontology": "PECO",
                "source": "OntologyService",
            }
        ]

    async def test_the_ontology_catalog_can_be_listed(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00005", email="ont00005@example.org"
        )
        catalog = {
            "_embedded": {
                "ontologies": [
                    {
                        "ontologyId": "go",
                        "config": {
                            "title": "Gene Ontology",
                            "preferredPrefix": "GO",
                            "description": "An ontology for gene products.",
                        },
                    }
                ]
            },
            "page": {"totalElements": 1},
        }

        listing = await _tool(server, "list_ontologies")
        with (
            patch(
                "metaseed_hub.mcp._ontology_tools._fetch_ontologies",
                return_value=catalog,
            ),
            _calling_with(secret),
        ):
            result = json.loads(await listing())

        assert result["total"] == 1
        assert result["ontologies"] == [
            {
                "id": "go",
                "name": "Gene Ontology",
                "prefix": "GO",
                "description": "An ontology for gene products.",
            }
        ]

    async def test_an_unreachable_catalog_is_an_error(self, server, session: AsyncSession) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00006", email="ont00006@example.org"
        )

        listing = await _tool(server, "list_ontologies")
        with (
            patch("metaseed_hub.mcp._ontology_tools._fetch_ontologies", return_value=None),
            _calling_with(secret),
            pytest.raises(ValueError, match="could not be reached"),
        ):
            await listing()


class TestAnOutageIsNotNonexistence:
    """The router swallows a failing source and answers None — the same answer
    as a genuinely missing term. `get_ontology_term` turned that None into
    "No ontology term", so an OLS outage told the agent the term does not
    exist. The tool now asks whether anyone could actually see the ontology
    before calling the term missing."""

    async def test_nobody_could_say_reads_as_could_not_check(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00007", email="ont00007@example.org"
        )

        class _DownRouter(_FakeRouter):
            def has_ontology_sync(self, ontology_id: str):
                return None  # no source could answer: outage or uncarried

        fake = _DownRouter(term=None)
        get_term = await _tool(server, "get_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_term_source", return_value=fake),
            _calling_with(secret),
            pytest.raises(ValueError, match="could not be checked"),
        ):
            await get_term("PATO:0000015")

    async def test_a_carried_ontology_without_the_term_is_still_missing(
        self, server, session: AsyncSession
    ) -> None:
        _t, _u, secret, _token = await _user_with_token(
            session, slug="ont00008", email="ont00008@example.org"
        )

        class _CarriedRouter(_FakeRouter):
            def has_ontology_sync(self, ontology_id: str):
                return True

        fake = _CarriedRouter(term=None)
        get_term = await _tool(server, "get_ontology_term")
        with (
            patch("metaseed_hub.mcp._ontology_tools.get_term_source", return_value=fake),
            _calling_with(secret),
            pytest.raises(ValueError, match="No ontology term"),
        ):
            await get_term("PATO:9999999")
