"""Datasets from public repository records, without naming them first.

The form is the **From a repository** tab of the New Dataset screen. A
submission starts a background job and answers with its panel; the panel polls
the job while it runs and shows each outcome as it is reached.

See docs/datasets/import-export.md, *From a public repository*.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from metaseed_hub.models import ImportJob
from metaseed_hub.ui.dependencies import CurrentUser, DbSession, ensure_tenant_and_user
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error
from metaseed_hub.ui.services.repository_import import (
    MAX_IDENTIFIERS,
    job_of,
    parse_identifiers,
    repositories,
    start_job,
    summary,
)

router = APIRouter(prefix="/import", tags=["import"])


def job_panel(request: Request, user: Any, job: ImportJob) -> Response:
    """The job as the page shows it, telling the page what has been imported so far."""
    response = render_template(request, "partials/import_job.html", {"user": user, "job": job})
    imported = [
        {"id": outcome["dataset_id"], "name": outcome["name"]}
        for outcome in job.outcomes
        if outcome["status"] == "imported"
    ]
    # After the swap, so the toasts follow what the page now shows.
    response.headers["HX-Trigger-After-Swap"] = json.dumps(
        {
            "importJobProgress": {
                "job": job.id,
                "status": job.status,
                "done": len(job.outcomes),
                "total": len(job.identifiers),
                "imported": imported,
                "summary": summary(job.outcomes),
            }
        }
    )
    return response


@router.post("/jobs", response_class=HTMLResponse)
async def start_import_job(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    profile: Annotated[str, Form()],
    identifiers: Annotated[str, Form()] = "",
) -> Response:
    """Start a job for the list, and answer with its panel."""
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
    if problem:
        # Answered 200 with the reason in it: htmx leaves an error status
        # unswapped, and the form would then appear to do nothing.
        return render_template(
            request, "partials/import_job.html", {"user": user, "problem": problem}
        )

    tenant, db_user = await ensure_tenant_and_user(session, user)
    job = await start_job(
        session, tenant_id=tenant.id, user_id=db_user.id, profile=profile, identifiers=wanted
    )
    await session.commit()
    request.app.state.import_jobs.start(job.id, user)
    return job_panel(request, user, job)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def import_job(
    request: Request, session: DbSession, user: CurrentUser, job_id: str
) -> Response:
    """The job's panel, as the page polls it."""
    _tenant, db_user = await ensure_tenant_and_user(session, user)
    job = await job_of(session, job_id, db_user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such import.")
    return job_panel(request, user, job)
