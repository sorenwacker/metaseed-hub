"""Ontology Lookup Service (OLS) API routes.

Provides endpoints for searching and retrieving ontology terms from EMBL-EBI OLS4.
Uses metaseed's OntologyService for caching and rate limiting.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from metaseed.services import get_ontology_service

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
    service = get_ontology_service()

    try:
        results = await service.search(q, ontology=ontology, rows=rows)
        return JSONResponse(
            content={
                "query": q,
                "ontology": ontology,
                "total_found": len(results),
                "results": [
                    {
                        "value": r.term_id,
                        "label": r.label,
                        "ontology": r.ontology,
                        "iri": r.iri,
                        "description": r.description,
                    }
                    for r in results
                ],
            }
        )
    except Exception as e:
        logger.warning("OLS search failed: %s", e)
        return JSONResponse(
            content={"error": "Failed to search OLS4"},
            status_code=502,
        )


@router.get("/suggest")
async def suggest_ontology_terms(
    q: Annotated[str, Query(description="Partial term for autocomplete", min_length=2)],
    ontology: Annotated[str | None, Query(description="Filter by ontology ID")] = None,
) -> JSONResponse:
    """Get autocomplete suggestions for ontology terms.

    Uses the same search endpoint but returns fewer fields for faster response.

    Args:
        q: Partial term to get suggestions for (e.g., "drou" for drought)
        ontology: Optional ontology ID to filter results

    Returns:
        JSON with suggested terms including id, label, and ontology.
    """
    service = get_ontology_service()

    try:
        results = await service.search(q, ontology=ontology, rows=10)
        return JSONResponse(
            content={
                "query": q,
                "ontology": ontology,
                "suggestions": [
                    {
                        "id": r.term_id,
                        "label": r.label,
                        "ontology": r.ontology,
                    }
                    for r in results
                ],
            }
        )
    except Exception as e:
        logger.warning("OLS suggest failed: %s", e)
        return JSONResponse(
            content={"error": "Failed to get suggestions"},
            status_code=502,
        )


@router.get("/term/{term_id:path}")
async def get_ontology_term(term_id: str) -> JSONResponse:
    """Get detailed information about a specific ontology term.

    Args:
        term_id: Term identifier in CURIE format (e.g., "PATO:0000015", "GO:0008150")

    Returns:
        JSON with term details including label, definition, synonyms, etc.
    """
    service = get_ontology_service()

    try:
        term = await service.get_term(term_id)
        if term is None:
            return JSONResponse(
                content={"error": f"Term not found: {term_id}"},
                status_code=404,
            )

        result = {
            "id": term.id,
            "label": term.label,
            "ontology": term.ontology,
            "iri": term.iri,
            "is_obsolete": term.is_obsolete,
        }

        if term.definition:
            result["definition"] = term.definition

        if term.synonyms:
            result["synonyms"] = term.synonyms

        return JSONResponse(content=result)
    except Exception as e:
        logger.warning("OLS get_term failed: %s", e)
        return JSONResponse(
            content={"error": f"Failed to get term: {term_id}"},
            status_code=502,
        )


@router.get("/cache/stats")
async def get_cache_stats() -> JSONResponse:
    """Get ontology cache statistics.

    Returns:
        JSON with cache hit/miss counts and size.
    """
    service = get_ontology_service()
    stats = service.get_cache_stats()
    return JSONResponse(content=stats)
