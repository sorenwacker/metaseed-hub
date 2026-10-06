"""The SEEK plugin: push a dataset to FAIRDOM-SEEK.

Import is deliberately absent from this version: ``import_from_seek`` derives
its profile from the SEEK instance, and hub datasets are bound to installed
profiles — a swapped-in derived facade would fail to load on the next request.
Import arrives when derived specs can be persisted (the spec-draft store is the
likely home).

The routes are open to every signed-in user; the connection they configure
(the API key) is the only gate, and nothing works until it is set. The heavy
lifting is metaseed's (:mod:`metaseed.seek`); these routes wrap it around the
hub's per-user connection and dataset model.

``/hub/seek`` is the page that holds the steps in order: the connection, the
project, and the datasets that can be pushed, each with its actions.

The connection is per user because SEEK creates every record as the API key's
person. The key is encrypted at rest and never rendered back into a page.
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from metaseed_hub.auth import TokenUser
from metaseed_hub.crypto import decrypt_secret, encrypt_secret
from metaseed_hub.models import Dataset, SeekConnection
from metaseed_hub.ui.dependencies import (
    DbSession,
    ensure_tenant_and_user,
    get_dataset_for_user,
    require_user,
)
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade
from metaseed_hub.ui.helpers.spec_hash import dataset_profile_spec
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import validate_csrf_or_error
from metaseed_hub.ui.services.dataset_listing import datasets_visible_to
from metaseed_hub.ui.services.seek_connection import connection_for_user, tenant_for_user

if TYPE_CHECKING:
    from metaseed.specs.schema import ProfileSpec
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/seek", tags=["seek"])

# SEEK is an adapter: reachable to every signed-in user, usable only once
# they configure their own SEEK connection (the API key IS the gate).
SeekUser = Annotated[TokenUser, Depends(require_user)]


#: How long a push waits for one answer from SEEK. SEEK builds a Study or an
#: Assay together with its Sample Types in one request, which took 35 seconds on
#: a small instance; at the client's default of 30 the request succeeded in SEEK
#: and was reported here as "timed out".
PUSH_TIMEOUT_SECONDS = 180.0


def _client_for(connection: SeekConnection, *, timeout: float = 30.0) -> Any:
    """A metaseed SEEK client for a stored connection.

    Args:
        connection: The stored connection.
        timeout: Seconds to wait for each answer; a push passes
            :data:`PUSH_TIMEOUT_SECONDS`.

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
    return client_from_settings({"url": connection.url, "api_key": api_key}, timeout=timeout)


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
        if code >= 500:
            # "SEEK rejected GET /isa_tags (503): no available server" read as a
            # bad request; the instance was simply down, and every endpoint said
            # the same thing.
            return (
                f"{host} is not serving SEEK right now (HTTP {code}). The "
                "instance or the proxy in front of it is down — nothing to "
                "change here; try again once it is back."
            )
        if code == 404:
            return (
                f"{host} answered, but not as a SEEK API. Give the instance's "
                "base URL, without /api or a path."
            )
        return f"SEEK answered {code} for {host}."
    return f"Could not reach SEEK at {host}: {exc}"


#: The SEEK page, where the connection is edited and its standing shown.
SETTINGS_URL = "/hub/seek"


def _back(error: str | None = None) -> RedirectResponse:
    """Back to the SEEK page, carrying a message the page can show."""
    if error:
        return RedirectResponse(url=f"{SETTINGS_URL}?seek_error={quote(error)}", status_code=303)
    return RedirectResponse(url=SETTINGS_URL, status_code=303)


def _push_failure(exc: Exception, url: str) -> str:
    """Read a push failure the way the connection check reads one.

    A push meets the same instance and the same network, so the same four
    causes apply; anything else is reported as SEEK said it.
    """
    from httpx import HTTPStatusError

    message = _verification_failure(exc, url)
    if isinstance(exc, HTTPStatusError) or "not serving SEEK" in message:
        return message
    if message.startswith("Could not reach SEEK"):
        return str(exc)
    return message


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


async def _by_pushability(
    session: AsyncSession, datasets: list[Dataset]
) -> tuple[list[Dataset], int]:
    """Split datasets into those SEEK can take and a count of the rest.

    Many datasets share one specification, so each is resolved once.
    """
    supported: dict[tuple[str | None, str | None, str, str], bool] = {}
    pushable: list[Dataset] = []
    for dataset in datasets:
        key = (dataset.spec_draft_id, dataset.spec_id, dataset.profile, dataset.version)
        if key not in supported:
            supported[key] = spec_supports_seek(await dataset_profile_spec(session, dataset))
        if supported[key]:
            pushable.append(dataset)
    return pushable, len(datasets) - len(pushable)


@router.get("", response_class=HTMLResponse)
async def seek_page(request: Request, session: DbSession, user: SeekUser) -> Response:
    """The steps of a push in order: connection, project, datasets."""
    tenant, db_user = await ensure_tenant_and_user(session, user)
    datasets, _owned = await datasets_visible_to(session, tenant.id, db_user.id)
    pushable, unpushable = await _by_pushability(session, datasets)
    return render_template(
        request,
        "seek.html",
        {
            "user": user,
            "nav_active": "seek",
            "connection": await connection_for_user(session, user),
            "seek_error": request.query_params.get("seek_error"),
            "pushable": pushable,
            "unpushable": unpushable,
        },
    )


@router.get("/settings")
async def seek_settings(user: SeekUser) -> Response:
    """Send the old settings URL to the page that replaced it."""
    return RedirectResponse(url=SETTINGS_URL, status_code=302)


@router.post("/settings")
async def seek_settings_save(
    request: Request,
    session: DbSession,
    user: SeekUser,
    url: str = Form(...),
    api_key: str = Form(""),
    csrf_token: str | None = Form(None),
) -> Response:
    """Check the connection against SEEK, and store it either way.

    The check proves the instance answers and the key is accepted; it does not
    demand a project, because an account in no project is a working connection
    with something to fix in SEEK. Whatever the outcome, what was typed is
    saved with the result recorded, so a failed check never costs the key.
    """
    validate_csrf_or_error(request, csrf_token)
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
async def seek_settings_check(
    request: Request, session: DbSession, user: SeekUser, csrf_token: str | None = Form(None)
) -> Response:
    """Re-run the check against the stored connection, without retyping the key."""
    validate_csrf_or_error(request, csrf_token)
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


#: The ISA level a profile must declare before anything can be pushed. SEEK
#: hangs every record off an Investigation, so a profile without one has no
#: shape to map onto — which is true of every built-in except the SEEK-ready
#: template, including ENA and PRIDE.
REQUIRED_ROLE = "Investigation"


def spec_supports_seek(spec: ProfileSpec | None) -> bool:
    """Whether a dataset on this specification can be pushed to SEEK at all.

    Read from the specification's own SEEK role annotations rather than a list
    of names here, so a specification that declares the roles works without the
    hub being taught about it.

    Args:
        spec: The dataset's specification, as ``dataset_profile_spec`` resolves
            it, or None when it could not be resolved.

    Returns:
        True if an entity carries the Investigation role.
    """
    if spec is None:
        return False
    return any(
        entity.seek and entity.seek.role == REQUIRED_ROLE for entity in spec.entities.values()
    )


@router.get("/datasets/{dataset_id}/templates")
async def seek_isa_templates(dataset_id: str, session: DbSession, user: SeekUser) -> Response:
    """Download the ISA Templates of a dataset's specification.

    Sample Types and controlled vocabularies are provisioned by the push
    itself. Templates are not: only an administrator can install them, under
    *Templates -> populate*, and SEEK's ISA-JSON exporter reads them to tell an
    assay data file from an assay material. Without them a pushed dataset is in
    SEEK but cannot be exported as ISA-JSON, which is the whole point of
    putting it there.

    Addressed by dataset, not by profile name: a draft or a published
    specification lives in the database, where a name finds nothing.
    """
    from metaseed.seek.templates import to_isa_template_json

    dataset = await get_dataset_for_user(dataset_id, session, user)
    spec = await dataset_profile_spec(session, dataset)
    if spec is None or not spec_supports_seek(spec):
        # The same refusal the check and the push give: templates for a
        # specification that cannot be pushed would install and serve nothing.
        raise HTTPException(
            status_code=422,
            detail=f"The {dataset.profile} profile does not map onto SEEK.",
        )
    try:
        document = to_isa_template_json(spec)
    except Exception as exc:
        logger.info("ISA templates could not be built for %s: %s", dataset.profile, exc)
        raise HTTPException(
            status_code=422,
            detail=(
                "This profile has no material chain to build templates from — "
                "it needs entities that describe samples."
            ),
        ) from None

    stem = "".join(c for c in f"{dataset.profile}-{dataset.version}" if c.isalnum() or c in "-_.")
    return Response(
        json.dumps(document, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{stem}-isa-templates.json"'},
    )


@router.post("/project")
async def seek_choose_project(
    request: Request,
    session: DbSession,
    user: SeekUser,
    project_id: str = Form(...),
    csrf_token: str | None = Form(None),
) -> Response:
    """Set which SEEK project this person's pushes go to."""
    validate_csrf_or_error(request, csrf_token)
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


@router.post("/datasets/{dataset_id}/check", response_class=HTMLResponse)
async def seek_readiness(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: SeekUser,
    csrf_token: str | None = Form(None),
) -> Response:
    """Report what this SEEK still needs before a push of this dataset works.

    Three things fail separately and used to fail obscurely: the instance may
    not have ISA-JSON compliance switched on, the profile's ISA Templates may
    not be installed, and the connection may simply be unreachable. Naming
    which one saves reading a push failure backwards.
    """
    validate_csrf_or_error(request, csrf_token)
    dataset = await get_dataset_for_user(dataset_id, session, user)
    profile = await dataset_profile_spec(session, dataset)
    if profile is None or not spec_supports_seek(profile):
        return _panel(
            request,
            error=f"The {dataset.profile} profile does not map onto SEEK.",
        )

    connection = await connection_for_user(session, user)
    if connection is None:
        return _panel(request, error="Configure your SEEK connection first.")

    def work() -> tuple[list[str], list[str]]:
        from metaseed.seek.templates import template_title

        client = _client_for(connection)
        installed = set(client.template_ids_by_title())
        wanted = [
            template_title(profile, level) for level in ("study source", "study sample", "assay")
        ]
        return [t for t in wanted if t in installed], [t for t in wanted if t not in installed]

    try:
        present, missing = await run_in_threadpool(work)
    except Exception as exc:
        logger.info("SEEK readiness check failed: %s", exc)
        return _panel(request, error=_push_failure(exc, connection.url))

    if missing:
        return _panel(
            request,
            error=(
                f"{len(missing)} ISA Template(s) are not installed on this SEEK: "
                + ", ".join(missing)
                + ". Download them with the ISA templates button and have a "
                "SEEK administrator install them at "
                f"{connection.url}/templates/default_templates. That page "
                "exists only once 'Compliance with ISA-JSON schemas' is "
                "enabled, which itself needs Single page, ISA and Samples."
            ),
        )
    return _panel(request, message=f"Ready: {len(present)} template(s) installed.")


@router.post("/datasets/{dataset_id}/push", response_class=HTMLResponse)
async def seek_push(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: SeekUser,
    downloadable: bool = Form(False),
    csrf_token: str | None = Form(None),
) -> Response:
    """Provision the profile on SEEK and push the dataset.

    ``downloadable`` maps to SEEK's ``download`` sharing level — the level its
    ISA-JSON export requires. Off, SEEK's own default applies and the records
    stay private to the key's person.
    """
    validate_csrf_or_error(request, csrf_token)
    dataset = await get_dataset_for_user(dataset_id, session, user)
    # Before the connection: no SEEK account makes an unmappable profile work,
    # and "configure your connection first" would send someone to fix the wrong
    # thing.
    profile = await dataset_profile_spec(session, dataset)
    if profile is None or not spec_supports_seek(profile):
        return _panel(
            request,
            error=(
                f"The {dataset.profile} profile does not describe an ISA "
                "structure, so there is nothing for SEEK to hang these records "
                "on. Use a profile whose entities declare SEEK roles."
            ),
        )

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

        client = _client_for(connection, timeout=PUSH_TIMEOUT_SECONDS)
        # The person's choice; only fall back when they have never chosen.
        project_id = connection.project_id or client.default_project_id()
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
        return _panel(request, error=f"Push failed. {_push_failure(exc, connection.url)}")
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
