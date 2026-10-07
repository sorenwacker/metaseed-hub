"""The Import page: datasets from public repository records, without naming them.

One page under the header's **Import**, three requests. The page itself; the
list of identifiers, answered with one pending row each; and one request per
row, which fetches the record and creates its dataset. The rows ask in turn, so
a slow repository delays the rows after it and nothing else, and each row shows
its outcome as it finishes.

See docs/datasets/import-export.md, *The Import page*.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from metaseed_hub.ui.dependencies import CurrentUser, DbSession, ensure_tenant_and_user
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error
from metaseed_hub.ui.services.repository_import import (
    MAX_IDENTIFIERS,
    Outcome,
    import_record,
    parse_identifiers,
    repositories,
)

router = APIRouter(prefix="/import", tags=["import"])


@router.get("", response_class=HTMLResponse)
async def import_page(request: Request, user: CurrentUser) -> Response:
    """The form: a repository and the identifiers to fetch from it."""
    return render_template(
        request,
        "import.html",
        {
            "user": user,
            "nav_active": "import",
            "repositories": repositories(),
            "max_identifiers": MAX_IDENTIFIERS,
        },
    )


@router.post("/rows", response_class=HTMLResponse)
async def import_rows(
    request: Request,
    user: CurrentUser,
    profile: Annotated[str, Form()],
    identifiers: Annotated[str, Form()] = "",
) -> Response:
    """One pending row per identifier; each row then asks for its own import."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    wanted = parse_identifiers(identifiers)
    problem = ""
    if profile not in {repository.profile for repository in repositories()}:
        problem = "Choose a repository from the list."
    elif not wanted:
        problem = "Enter at least one identifier."
    elif len(wanted) > MAX_IDENTIFIERS:
        problem = (
            f"{len(wanted)} identifiers were entered and one submission takes "
            f"{MAX_IDENTIFIERS}. Nothing was imported; split the list."
        )
    return render_template(
        request,
        "partials/import_rows.html",
        # Answered 200 with the reason in it: htmx leaves an error status
        # unswapped, and the form would then appear to do nothing.
        {"user": user, "profile": profile, "identifiers": wanted, "problem": problem},
    )


@router.post("/record", response_class=HTMLResponse)
async def import_one_record(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    profile: Annotated[str, Form()],
    identifier: Annotated[str, Form()],
) -> Response:
    """Fetch one record, create its dataset, and answer with the finished row."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    tenant, _ = await ensure_tenant_and_user(session, user)
    try:
        outcome = await import_record(session, tenant.id, profile, identifier.strip(), user)
    except LookupError:
        outcome = Outcome(
            identifier, "failed", detail="No importer is installed for that repository."
        )
    await session.commit()
    return render_template(request, "partials/import_row.html", {"user": user, "outcome": outcome})
