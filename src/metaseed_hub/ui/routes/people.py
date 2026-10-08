"""People: who is in the collaborations you are in.

Names and addresses, so a collaboration's list is shown only to its members;
the snapshot from the last sign-in decides who those are. A member may keep
their own name and address from a collaboration, per collaboration, from here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from metaseed_hub.collaborations import (
    NotInCollaborationError,
    collaborations_of,
    membership_state,
    opted_out_of,
    people_in,
    set_opt_out,
)
from metaseed_hub.ui.dependencies import CurrentUser, DbSession, ensure_tenant_and_user
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

router = APIRouter(prefix="/people", tags=["people"])


async def _card_context(session: DbSession, viewer_id: str, collaboration: Any) -> dict[str, Any]:
    return {
        "collaboration": collaboration,
        "members": await people_in(session, collaboration.urn, viewer_id=viewer_id),
        "opted_out": await opted_out_of(session, viewer_id),
        "viewer_id": str(viewer_id),
    }


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
            "membership_state": await membership_state(session, db_user.id),
            "cards": [await _card_context(session, db_user.id, c) for c in collaborations],
        },
    )


@router.post("/{urn}/visibility", response_class=HTMLResponse)
async def set_visibility(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    urn: str,
    hidden: Annotated[str | None, Form()] = None,
) -> Response:
    """Keep the viewer's name and address from one collaboration, or stop.

    A tick box sends ``hidden`` when ticked and nothing when not, so the
    field's absence is the choice to be listed again. Answers with the
    collaboration's card as it now reads.
    """
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()
    _tenant, db_user = await ensure_tenant_and_user(session, user)
    try:
        await set_opt_out(session, db_user.id, urn, hidden=hidden is not None)
    except NotInCollaborationError as refused:
        raise HTTPException(status_code=404, detail="Not a collaboration you are in.") from refused
    await session.commit()
    collaboration = next(
        (c for c in await collaborations_of(session, db_user.id) if c.urn == urn), None
    )
    if collaboration is None:
        raise HTTPException(status_code=404, detail="Not a collaboration you are in.")
    return render_template(
        request,
        "partials/collaboration_card.html",
        {"user": user, **await _card_context(session, db_user.id, collaboration)},
    )
