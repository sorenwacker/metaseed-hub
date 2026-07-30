"""Ontology lookup tools for the hub's MCP endpoint.

Read-only lookups against EMBL-EBI OLS4, through the same
:class:`~metaseed.services.ontology.OntologyService` the web UI's ontology
routes use, so MCP callers share its cache and rate limiting. Nothing here
touches a dataset, so the tools are scoped to authentication only — but they
are authenticated: an open endpoint would let an unauthenticated caller drive
traffic through the hub (the same reasoning as ``ui/routes/ontology_api.py``).

The registrar takes the shared caller helper as an argument rather than
importing it from the package, so the package can import this module without a
cycle.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import httpx
from metaseed.services import get_ontology_service
from metaseed.services.ontology import OntologyServiceError

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import User

    Caller = Callable[[], AbstractAsyncContextManager[tuple[AsyncSession, User]]]

REQUEST_TIMEOUT = 30.0


async def _fetch_ontologies(base_url: str, rows: int) -> dict[str, Any] | None:
    """The OLS4 ontology catalog page, or None when it is unreachable.

    The shared OntologyService covers term search and lookup but has no
    catalog call, so this one request goes directly to the same OLS4
    deployment the service is configured for.

    Args:
        base_url: The OLS4 API base URL, from the service.
        rows: How many ontologies to request.
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{base_url}/ontologies", params={"size": rows})
            response.raise_for_status()
            return cast("dict[str, Any]", response.json())
    except httpx.HTTPError:
        return None


def register_ontology_tools(mcp: FastMCP, *, caller: Caller) -> None:
    """Register the ontology lookup tools with the hub's MCP server.

    Args:
        mcp: The FastMCP server to add the tools to.
        caller: Async context manager resolving the current call's token to a
            ``(session, user)`` pair; used here for authentication only.
    """

    @mcp.tool()
    async def search_ontology(query: str, ontology: str | None = None, rows: int = 10) -> str:
        """Search OLS4 for ontology terms matching a query.

        Searches term labels, synonyms, and descriptions. Use this to find a
        term for a concept you can name but cannot yet identify.

        Args:
            query: What to search for, e.g. "drought" or "plant height".
            ontology: Restrict to one ontology id, e.g. "pato" or "go".
                Omit to search across all of them.
            rows: Maximum number of results (1-100).
        """
        rows = min(max(1, rows), 100)
        async with caller():
            pass  # Authentication only; a lookup touches nobody's data.

        results = await get_ontology_service().search(query, ontology=ontology, rows=rows)
        reported = []
        for r in results:
            item: dict[str, Any] = {
                "id": r.term_id,
                "label": r.label,
                "ontology": r.ontology,
                "iri": r.iri,
            }
            if r.description:
                item["description"] = r.description
            reported.append(item)
        return json.dumps(
            {
                "query": query,
                "ontology": ontology,
                "total_found": len(reported),
                "results": reported,
            }
        )

    @mcp.tool()
    async def get_ontology_term(term_id: str) -> str:
        """Return one ontology term's definition and synonyms.

        Args:
            term_id: The term in CURIE form, e.g. "PATO:0000015" or
                "GO:0008150".
        """
        async with caller():
            pass  # Authentication only; a lookup touches nobody's data.

        try:
            term = await get_ontology_service().get_term(term_id)
        except OntologyServiceError as e:
            raise ValueError(f"The ontology service could not be reached: {e}") from e
        if term is None:
            raise ValueError(
                f"No ontology term {term_id!r}. Term ids look like 'PATO:0000015'; "
                "find one with search_ontology."
            )

        result: dict[str, Any] = {
            "id": term.term_id,
            "label": term.label,
            "ontology": term.ontology,
            "iri": term.iri,
        }
        if term.description:
            result["definition"] = term.description
        if term.synonyms:
            result["synonyms"] = term.synonyms
        return json.dumps(result)

    @mcp.tool()
    async def list_ontologies(rows: int = 50) -> str:
        """List the ontologies available in OLS4, with ids and names.

        Use this to discover which ontology ids search_ontology accepts.

        Args:
            rows: Maximum number of ontologies to return (1-500).
        """
        rows = min(max(1, rows), 500)
        async with caller():
            pass  # Authentication only; a lookup touches nobody's data.

        data = await _fetch_ontologies(get_ontology_service().base_url, rows)
        if data is None:
            raise ValueError("The ontology catalog could not be reached; try again later.")

        reported = []
        for ontology in data.get("_embedded", {}).get("ontologies", []):
            config = ontology.get("config", {})
            item: dict[str, Any] = {
                "id": ontology.get("ontologyId"),
                "name": config.get("title") or config.get("preferredPrefix"),
                "prefix": config.get("preferredPrefix"),
            }
            if config.get("description"):
                item["description"] = config["description"]
            reported.append(item)
        total = data.get("page", {}).get("totalElements", len(reported))
        return json.dumps({"total": total, "ontologies": reported})

    @mcp.tool()
    async def suggest_ontology_term(query: str, ontology: str | None = None) -> str:
        """Suggest ontology terms for a partial query, with ids and labels only.

        A lighter answer than search_ontology, for completing a term you have
        already partly typed.

        Args:
            query: The partial term, e.g. "drou" for drought.
            ontology: Restrict to one ontology id, e.g. "pato" or "go".
        """
        async with caller():
            pass  # Authentication only; a lookup touches nobody's data.

        results = await get_ontology_service().search(query, ontology=ontology, rows=10)
        suggestions = [{"id": r.term_id, "label": r.label, "ontology": r.ontology} for r in results]
        return json.dumps({"query": query, "ontology": ontology, "suggestions": suggestions})
