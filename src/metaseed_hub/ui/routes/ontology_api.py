"""Ontology Lookup Service (OLS) API routes.

Provides endpoints for searching and retrieving ontology terms from EMBL-EBI OLS4.
Uses the same OLS4 API integration as metaseed's MCP tools.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from metaseed.agent.mcp.tools.ontology import _make_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ontology", tags=["ontology"])

__all__ = ["router"]


@router.get("/search")
async def search_ontology_terms(
    q: Annotated[str, Query(description="Search query", min_length=2)],
    ontology: Annotated[str | None, Query(description="Filter by ontology ID")] = None,
    rows: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JSONResponse:
    """Search for ontology terms matching a query.

    Args:
        q: Search query (e.g., "drought", "plant growth")
        ontology: Optional ontology ID to filter results (e.g., "pato", "go", "obi")
        rows: Number of results to return (1-100)

    Returns:
        JSON with matching terms including id, label, ontology, and description.
    """
    rows = min(max(1, rows), 100)

    params = {
        "q": q,
        "rows": rows,
        "fieldList": "iri,label,short_form,obo_id,ontology_name,ontology_prefix,description",
    }

    if ontology:
        params["ontology"] = ontology.lower()

    data = _make_request("/search", params)
    if data is None:
        return JSONResponse(
            content={"error": "Failed to search OLS4"},
            status_code=502,
        )

    response = data.get("response", {})
    docs = response.get("docs", [])

    results = []
    for doc in docs:
        result = {
            "id": doc.get("obo_id") or doc.get("short_form"),
            "label": doc.get("label"),
            "ontology": doc.get("ontology_prefix") or doc.get("ontology_name"),
            "iri": doc.get("iri"),
        }
        if doc.get("description"):
            descriptions = doc["description"]
            if isinstance(descriptions, list) and descriptions:
                result["description"] = descriptions[0]
            elif isinstance(descriptions, str):
                result["description"] = descriptions
        results.append(result)

    return JSONResponse(
        content={
            "query": q,
            "ontology": ontology,
            "total_found": response.get("numFound", 0),
            "results": results,
        }
    )


@router.get("/suggest")
async def suggest_ontology_terms(
    q: Annotated[str, Query(description="Partial term for autocomplete", min_length=2)],
    ontology: Annotated[str | None, Query(description="Filter by ontology ID")] = None,
) -> JSONResponse:
    """Get autocomplete suggestions for ontology terms.

    Faster than search, optimized for real-time autocomplete.

    Args:
        q: Partial term to get suggestions for (e.g., "drou" for drought)
        ontology: Optional ontology ID to filter results

    Returns:
        JSON with suggested terms including id, label, and ontology.
    """
    params = {"q": q}

    if ontology:
        params["ontology"] = ontology.lower()

    data = _make_request("/select", params)
    if data is None:
        return JSONResponse(
            content={"error": "Failed to get suggestions"},
            status_code=502,
        )

    response = data.get("response", {})
    docs = response.get("docs", [])

    suggestions = []
    for doc in docs:
        suggestion = {
            "id": doc.get("obo_id") or doc.get("short_form"),
            "label": doc.get("label"),
            "ontology": doc.get("ontology_prefix") or doc.get("ontology_name"),
        }
        suggestions.append(suggestion)

    return JSONResponse(
        content={
            "query": q,
            "ontology": ontology,
            "suggestions": suggestions,
        }
    )


@router.get("/term/{term_id:path}")
async def get_ontology_term(term_id: str) -> JSONResponse:
    """Get detailed information about a specific ontology term.

    Args:
        term_id: Term identifier in CURIE format (e.g., "PATO:0000015", "GO:0008150")
                 or full IRI format.

    Returns:
        JSON with term details including label, definition, synonyms, etc.
    """
    # Determine if it's a CURIE or IRI
    if term_id.startswith("http://") or term_id.startswith("https://"):
        iri = term_id
        # Try to extract ontology from IRI
        if "/obo/" in iri:
            parts = iri.split("/obo/")[-1].split("_")
            ontology = parts[0].lower() if parts else None
        else:
            ontology = None
    elif ":" in term_id:
        prefix, local_id = term_id.split(":", 1)
        ontology = prefix.lower()
        iri = f"http://purl.obolibrary.org/obo/{prefix}_{local_id}"
    else:
        return JSONResponse(
            content={"error": f"Invalid term ID format: {term_id}"},
            status_code=400,
        )

    if not ontology:
        return JSONResponse(
            content={"error": "Could not determine ontology from term ID"},
            status_code=400,
        )

    # URL encode the IRI twice (OLS4 requirement)
    encoded_iri = urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")

    data = _make_request(f"/ontologies/{ontology}/terms/{encoded_iri}")
    if data is None:
        return JSONResponse(
            content={"error": f"Term not found: {term_id}"},
            status_code=404,
        )

    result = {
        "id": data.get("obo_id") or data.get("short_form"),
        "label": data.get("label"),
        "ontology": data.get("ontology_prefix") or data.get("ontology_name"),
        "iri": data.get("iri"),
        "is_obsolete": data.get("is_obsolete", False),
    }

    if data.get("description"):
        descriptions = data["description"]
        if isinstance(descriptions, list) and descriptions:
            result["definition"] = descriptions[0]
        elif isinstance(descriptions, str):
            result["definition"] = descriptions

    if data.get("synonyms"):
        result["synonyms"] = data["synonyms"]

    if data.get("annotation"):
        annotations = data["annotation"]
        if "has_obo_namespace" in annotations:
            result["namespace"] = annotations["has_obo_namespace"]
        if "created_by" in annotations:
            result["created_by"] = annotations["created_by"]

    return JSONResponse(content=result)


@router.get("/ontologies")
async def list_ontologies(
    rows: Annotated[int, Query(ge=1, le=500)] = 50,
) -> JSONResponse:
    """List available ontologies in OLS4.

    Args:
        rows: Number of ontologies to return (1-500)

    Returns:
        JSON with available ontologies including id, name, prefix, and description.
    """
    rows = min(max(1, rows), 500)

    data = _make_request("/ontologies", {"size": rows})
    if data is None:
        return JSONResponse(
            content={"error": "Failed to list ontologies"},
            status_code=502,
        )

    embedded = data.get("_embedded", {})
    ontologies = embedded.get("ontologies", [])

    results = []
    for ont in ontologies:
        config = ont.get("config", {})
        result = {
            "id": ont.get("ontologyId"),
            "name": config.get("title") or config.get("preferredPrefix"),
            "prefix": config.get("preferredPrefix"),
        }
        if config.get("description"):
            result["description"] = config["description"]
        if config.get("homepage"):
            result["homepage"] = config["homepage"]
        results.append(result)

    page_info = data.get("page", {})

    return JSONResponse(
        content={
            "total": page_info.get("totalElements", len(results)),
            "ontologies": results,
        }
    )
