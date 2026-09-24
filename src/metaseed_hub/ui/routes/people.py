"""People: who is in the collaborations you are in.

Names and addresses, so a collaboration's list is shown only to its members;
the snapshot from the last sign-in decides who those are.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from metaseed_hub.collaborations import collaborations_of, people_in
from metaseed_hub.ui.dependencies import CurrentUser, DbSession, ensure_tenant_and_user
from metaseed_hub.ui.render import render_template

router = APIRouter(prefix="/people", tags=["people"])


@router.get("", response_class=HTMLResponse)
async def people(request: Request, session: DbSession, user: CurrentUser) -> Response:
    """Every collaboration the viewer is in, each with its signed-in members."""
    _tenant, db_user = await ensure_tenant_and_user(session, user)
    collaborations = await collaborations_of(session, db_user.id)
    return render_template(
        request,
        "people.html",
        {
            "user": user,
            "nav_active": "people",
            "collaborations": [
                (c, await people_in(session, c.urn, viewer_id=db_user.id)) for c in collaborations
            ],
        },
    )
