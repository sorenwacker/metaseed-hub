"""The SEEK plugin: push a dataset to FAIRDOM-SEEK.

Import is deliberately absent from this version: ``import_from_seek`` derives
its profile from the SEEK instance, and hub datasets are bound to installed
profiles — a swapped-in derived facade would fail to load on the next request.
Import arrives when derived specs can be persisted (the spec-draft store is the
likely home).

Every route requires the ``seek`` feature — membership of the seek group is
what makes this exist, which is how a plugin is developed in production while
visible only to its testers. The heavy lifting is metaseed's
(:mod:`metaseed.seek`); these routes wrap it around the hub's per-user
connection and dataset model.

The connection is per user because SEEK creates every record as the API key's
person. The key is encrypted at rest and never rendered back into a page.
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from metaseed_hub.auth import TokenUser
from metaseed_hub.crypto import decrypt_secret, encrypt_secret
from metaseed_hub.models import SeekConnection
from metaseed_hub.ui.dependencies import (
    DbSession,
    get_dataset_for_user,
    require_feature,
)
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.services.seek_connection import connection_for_user, tenant_for_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/seek", tags=["seek"])

SeekUser = Annotated[TokenUser, Depends(require_feature("seek"))]


def _client_for(connection: SeekConnection) -> Any:
    """A metaseed SEEK client for a stored connection.

    Raises ``ValueError`` when the key cannot be decrypted — which in practice
    means ``SECRET_KEY`` changed since it was stored, and the remedy is
    re-entering it.
    """
    from metaseed.seek import client_from_settings

    api_key = decrypt_secret(connection.api_key_encrypted)
    if api_key is None:
        raise ValueError(
            "The stored SEEK API key cannot be read any more (the server "
            "secret changed). Enter it again on the SEEK settings page."
        )
    return client_from_settings({"url": connection.url, "api_key": api_key})


def _verification_failure(exc: Exception, url: str) -> str:
    """Say what actually went wrong, not that something did.

    "check the URL and the key" sent the owner hunting for a bad key when the
    real answer was a hostname the server cannot resolve. Each cause below has
    a different fix, so each gets its own sentence.
    """
    host = urlsplit(url).netloc or url
    text = str(exc)
    if isinstance(exc, socket.gaierror) or "name resolution" in text:
        return (
            f"The server cannot resolve {host}. A SEEK on your own machine or "
            "behind a VPN is not reachable from metaseed.ewi — it needs a "
            "hostname or address this server can see."
        )
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return (
            f"Nothing answered at {host}. Check the port and that the instance "
            "is running and reachable from this server."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "SEEK rejected the API key. Check it was copied whole and has not expired."
        if code == 404:
            return (
                f"{host} answered, but not as a SEEK API. Give the instance's "
                "base URL, without /api or a path."
            )
        return f"SEEK answered {code} for {host}."
    return f"Could not reach SEEK at {host}: {exc}"


#: Where the connection is edited and its standing shown.
SETTINGS_URL = "/hub/auth/profile#seek"


def _back(error: str | None = None) -> RedirectResponse:
    """Back to the settings section, carrying a message the page can show."""
    if error:
        return RedirectResponse(
            url=f"/hub/auth/profile?seek_error={quote(error)}#seek", status_code=303
        )
    return RedirectResponse(url=SETTINGS_URL, status_code=303)


def _record_outcome(
    connection: SeekConnection, error: str | None, projects: list[tuple[str, str]]
) -> None:
    """Write down what the check found.

    ``verified_at`` marks the last time SEEK answered and took the key.
    ``last_error`` holds anything that would stop a push — including an account
    in no project, which is a working connection that cannot receive anything
    yet, because SEEK attaches every record to a project.
    """
    connection.verified_at = None if error else datetime.now(UTC)
    if error:
        connection.last_error = error
    elif not projects:
        connection.last_error = (
            "The API key works, but your SEEK account is in no project, and "
            "SEEK attaches every record to one. Join or create a project in "
            "SEEK, then check again."
        )
    else:
        connection.last_error = None
    if projects:
        connection.projects = [[str(pid), str(title)] for pid, title in projects]
        # Keep the person's choice if it still exists; otherwise fall back to
        # the first, which is what the push did before anyone could choose.
        chosen = {str(pid) for pid, _ in projects}
        if connection.project_id not in chosen:
            connection.project_id = str(projects[0][0])
        connection.project_hint = next(
            title for pid, title in connection.projects if pid == connection.project_id
        )


@router.get("")
@router.get("/settings")
async def seek_settings(user: SeekUser) -> Response:
    """Send the old settings URLs to the profile section that replaced them."""
    return RedirectResponse(url=SETTINGS_URL, status_code=302)


@router.post("/settings")
async def seek_settings_save(
    request: Request,
    session: DbSession,
    user: SeekUser,
    url: str = Form(...),
    api_key: str = Form(""),
) -> Response:
    """Check the connection against SEEK, and store it either way.

    The check proves the instance answers and the key is accepted; it does not
    demand a project, because an account in no project is a working connection
    with something to fix in SEEK. Whatever the outcome, what was typed is
    saved with the result recorded, so a failed check never costs the key.
    """
    from metaseed.seek import client_from_settings

    url = url.strip().rstrip("/")
    api_key = api_key.strip()

    # The key is never rendered back into the page, so the box is always empty
    # — which meant correcting a URL cost you the key. Blank now means keep the
    # stored one; only a first connection has to supply it.
    stored = await connection_for_user(session, user)
    if not api_key:
        kept = decrypt_secret(stored.api_key_encrypted) if stored else None
        api_key = kept or ""
        if not api_key:
            return _back(
                "Enter the API key — there is no stored one to keep."
                if stored is None
                else "The stored key cannot be read any more; enter it again."
            )

    error = None
    projects: list[tuple[str, str]] = []
    try:
        projects = await run_in_threadpool(
            lambda: client_from_settings({"url": url, "api_key": api_key}).list_projects()
        )
    except Exception as exc:
        logger.info("SEEK verification failed for %s: %s", urlsplit(url).netloc, exc)
        error = _verification_failure(exc, url)

    tenant = await tenant_for_user(session, user)
    if tenant is None:  # pragma: no cover - a signed-in user has a tenant
        return _panel(request, error="No tenant for this account.")

    # Stored either way. A rejected save meant retyping the API key to correct a
    # typo in the URL, and losing a working key to a SEEK that happened to be
    # down. What the check found is recorded instead of thrown away.
    connection = await connection_for_user(session, user)
    if connection is None:
        connection = SeekConnection(tenant_id=tenant.id, url=url, api_key_encrypted="")
        session.add(connection)
    connection.url = url
    connection.api_key_encrypted = encrypt_secret(api_key)
    _record_outcome(connection, error, projects)
    await session.commit()

    return RedirectResponse(url=SETTINGS_URL, status_code=303)


@router.post("/settings/check")
async def seek_settings_check(session: DbSession, user: SeekUser) -> Response:
    """Re-run the check against the stored connection, without retyping the key."""
    connection = await connection_for_user(session, user)
    if connection is None:  # nothing stored yet — the form is where to start
        return RedirectResponse(url=SETTINGS_URL, status_code=303)

    error = None
    projects: list[tuple[str, str]] = []
    try:
        projects = await run_in_threadpool(lambda: _client_for(connection).list_projects())
    except Exception as exc:
        logger.info("SEEK re-check failed for %s: %s", urlsplit(connection.url).netloc, exc)
        error = _verification_failure(exc, connection.url)

    _record_outcome(connection, error, projects)
    await session.commit()

    return RedirectResponse(url=SETTINGS_URL, status_code=303)


@router.get("/templates/{profile}/{version}")
async def seek_isa_templates(profile: str, version: str, user: SeekUser) -> Response:
    """Download a profile's ISA Templates, for a SEEK administrator to install.

    Sample Types and controlled vocabularies are provisioned by the push
    itself. Templates are not: only an administrator can install them, under
    *Templates -> populate*, and SEEK's ISA-JSON exporter reads them to tell an
    assay data file from an assay material. Without them a pushed dataset is in
    SEEK but cannot be exported as ISA-JSON, which is the whole point of
    putting it there.
    """
    from metaseed.seek.templates import to_isa_template_json
    from metaseed.specs.loader import SpecLoader

    try:
        spec = SpecLoader().load_profile(version, profile)
    except Exception:
        # Never echo the requested profile back into the response.
        raise HTTPException(status_code=404, detail="Unknown profile") from None

    try:
        document = to_isa_template_json(spec)
    except Exception as exc:
        logger.info("ISA templates could not be built for %s: %s", profile, exc)
        raise HTTPException(
            status_code=422,
            detail=(
                "This profile has no material chain to build templates from — "
                "it needs entities that describe samples."
            ),
        ) from None

    stem = "".join(c for c in f"{profile}-{version}" if c.isalnum() or c in "-_.")
    return Response(
        json.dumps(document, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{stem}-isa-templates.json"'},
    )


@router.post("/project")
async def seek_choose_project(
    session: DbSession, user: SeekUser, project_id: str = Form(...)
) -> Response:
    """Set which SEEK project this person's pushes go to."""
    connection = await connection_for_user(session, user)
    if connection is None:
        return _back("Configure your SEEK connection first.")

    known = {pid: title for pid, title in connection.projects}
    if project_id not in known:
        # The list comes from the page, which may be stale if projects changed
        # in SEEK since the last check.
        return _back("That project is not on your SEEK any more — check again.")

    connection.project_id = project_id
    connection.project_hint = known[project_id]
    await session.commit()
    return _back()


@router.post("/datasets/{dataset_id}/push", response_class=HTMLResponse)
async def seek_push(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: SeekUser,
    downloadable: bool = Form(False),
) -> Response:
    """Provision the profile on SEEK and push the dataset.

    ``downloadable`` maps to SEEK's ``download`` sharing level — the level its
    ISA-JSON export requires. Off, SEEK's own default applies and the records
    stay private to the key's person.
    """
    dataset = await get_dataset_for_user(dataset_id, session, user)
    connection = await connection_for_user(session, user)
    if connection is None:
        return _panel(request, error="Configure your SEEK connection first.")

    state = await ensure_dataset_facade(dataset, session)
    facade = state.get_or_create_facade()

    def work() -> Any:
        from metaseed import MetaseedClient
        from metaseed.seek import (
            build_provisioning_plan,
            execute_provisioning_plan,
            sync_dataset_to_seek,
        )
        from metaseed.seek.provision import resolve_cv_ids
        from metaseed.specs.loader import SpecLoader

        client = _client_for(connection)
        # The person's choice; only fall back when they have never chosen.
        project_id = connection.project_id or client.default_project_id()
        profile = SpecLoader().load_profile(dataset.version, dataset.profile)
        execute_provisioning_plan(client, build_provisioning_plan(profile), project_id=project_id)
        return sync_dataset_to_seek(
            client,
            MetaseedClient.from_facade(facade),
            project_id=project_id,
            cv_ids=resolve_cv_ids(client, profile),
            sharing="download" if downloadable else None,
        )

    try:
        result = await run_in_threadpool(work)
    except Exception as exc:
        logger.info("SEEK push failed: %s", exc)
        return _panel(request, error=f"Push failed: {exc}")
    return _panel(request, result=result)


def _panel(
    request: Request,
    result: Any = None,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    return render_template(
        request,
        "partials/seek_panel_result.html",
        {"result": result, "message": message, "error": error},
    )
