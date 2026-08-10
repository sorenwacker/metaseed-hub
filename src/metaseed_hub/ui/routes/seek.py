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

import logging
import socket
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
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


@router.get("/settings", response_class=HTMLResponse)
async def seek_settings(request: Request, session: DbSession, user: SeekUser) -> Response:
    """The connection form. Shows configured-or-not, never the key."""
    connection = await connection_for_user(session, user)
    return render_template(
        request,
        "seek_settings.html",
        {
            "user": user,
            "connection": connection,
            "configured_url": connection.url if connection else None,
            "message": None,
            "error": None,
            "nav_active": "seek",
        },
    )


@router.post("/settings", response_class=HTMLResponse)
async def seek_settings_save(
    request: Request,
    session: DbSession,
    user: SeekUser,
    url: str = Form(...),
    api_key: str = Form(...),
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
    connection.verified_at = None if error else datetime.now(UTC)
    connection.last_error = error
    await session.commit()

    message = None
    if error is None:
        # Projects are listed rather than demanded: reaching SEEK with a valid
        # key is a working connection, and being in no project is a separate
        # thing to fix in SEEK — refusing the connection over it sent the owner
        # looking for a bad key that was never bad.
        if projects:
            message = f"Connected. Content will go to project {projects[0][1]}."
        else:
            message = (
                "Connected, and the API key works — but your SEEK account is in "
                "no project, and SEEK attaches every record to one. Join or "
                "create a project in SEEK before pushing."
            )

    return render_template(
        request,
        "seek_settings.html",
        {
            "user": user,
            "connection": connection,
            "configured_url": url,
            "message": message,
            "error": error,
            "nav_active": "seek",
        },
    )


@router.post("/settings/check", response_class=HTMLResponse)
async def seek_settings_check(request: Request, session: DbSession, user: SeekUser) -> Response:
    """Re-run the check against the stored connection, without retyping the key."""
    connection = await connection_for_user(session, user)
    if connection is None:
        return await seek_settings(request, session, user)

    error = None
    projects: list[tuple[str, str]] = []
    try:
        projects = await run_in_threadpool(lambda: _client_for(connection).list_projects())
    except Exception as exc:
        logger.info("SEEK re-check failed for %s: %s", urlsplit(connection.url).netloc, exc)
        error = _verification_failure(exc, connection.url)

    connection.verified_at = None if error else datetime.now(UTC)
    connection.last_error = error
    await session.commit()

    return render_template(
        request,
        "seek_settings.html",
        {
            "user": user,
            "connection": connection,
            "configured_url": connection.url,
            "message": None
            if error
            else (
                f"Working. Content will go to project {projects[0][1]}."
                if projects
                else "Reached SEEK, but your account is in no project."
            ),
            "error": error,
            "nav_active": "seek",
        },
    )


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
        project_id = client.default_project_id()
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
