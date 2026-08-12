"""Ontology Lookup Service (OLS) API routes.

Provides endpoints for searching and retrieving ontology terms from EMBL-EBI OLS4.
Uses metaseed's OntologyService for caching and rate limiting.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from metaseed.services.terms import get_term_source

from metaseed_hub.auth import TokenUser
from metaseed_hub.ui.dependencies import get_current_user_from_cookie

logger = logging.getLogger(__name__)


async def _require_user_json(request: Request) -> TokenUser:
    """Require an authenticated user, returning a JSON 401 rather than a redirect.

    These endpoints proxy outbound requests to EMBL-EBI OLS4. Leaving them open
    lets an unauthenticated caller drive traffic through the hub; gate them behind
    a valid session cookie. HTTPException keeps the failure a clean 401 under both
    the root and /hub mounts (the redirect-based auth handler is hub-only).
    """
    user = await get_current_user_from_cookie(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


router = APIRouter(
    prefix="/api/ontology",
    tags=["ontology"],
    dependencies=[Depends(_require_user_json)],
)

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

    try:
        hits = await get_term_source().search(q, ontology, rows)
        return JSONResponse(
            content={
                "query": q,
                "ontology": ontology,
                "total_found": len(hits),
                "results": [hit.to_dict() for hit in hits],
            }
        )
    except Exception as e:
        logger.warning("Term search failed: %s", e)
        return JSONResponse(
            content={"error": "Failed to search for terms"},
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
    try:
        hits = await get_term_source().search(q, ontology, 10)
        return JSONResponse(
            content={
                "query": q,
                "ontology": ontology,
                "suggestions": [
                    {
                        "id": hit.id,
                        "label": hit.label,
                        "ontology": hit.ontology,
                        "source": hit.source,
                    }
                    for hit in hits
                ],
            }
        )
    except Exception as e:
        logger.warning("Term suggestions failed: %s", e)
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
    try:
        term = await get_term_source().get_term(term_id)
        if term is None:
            return JSONResponse(
                content={"error": f"Term not found: {term_id}"},
                status_code=404,
            )

        # Fields vary by source: a local vocabulary has an id and a label, OLS
        # adds an IRI, synonyms and obsolescence. Report what the answering
        # source actually holds rather than inventing empty fields for the rest.
        result: dict[str, object] = {
            "id": getattr(term, "id", term_id),
            "label": getattr(term, "label", ""),
        }
        for name in ("ontology", "iri", "is_obsolete", "definition", "synonyms"):
            value = getattr(term, name, None)
            if value not in (None, "", []):
                result[name] = value
        source = getattr(term, "source", "")
        if source:
            result["source"] = source

        return JSONResponse(content=result)
    except Exception as e:
        logger.warning("Term lookup failed: %s", e)
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
    from metaseed.services import get_ontology_service

    # Cache statistics are about the OLS adapter specifically: it is the source
    # that caches, because it is the one that goes over the network.
    return JSONResponse(content=get_ontology_service().get_cache_stats())
