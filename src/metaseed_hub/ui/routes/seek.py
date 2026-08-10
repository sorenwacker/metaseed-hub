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
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from metaseed_hub.auth import TokenUser
from metaseed_hub.crypto import decrypt_secret, encrypt_secret
from metaseed_hub.models import SeekConnection, Tenant
from metaseed_hub.ui.dependencies import (
    DbSession,
    get_dataset_for_user,
    require_feature,
    tenant_slug_for,
)
from metaseed_hub.ui.helpers.dataset_state import ensure_dataset_facade
from metaseed_hub.ui.render import render_template

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/seek", tags=["seek"])

SeekUser = Annotated[TokenUser, Depends(require_feature("seek"))]


async def _tenant_for(session: Any, user: TokenUser) -> Tenant | None:
    result = await session.execute(
        select(Tenant).where(Tenant.slug == tenant_slug_for(user.keycloak_id))
    )
    tenant: Tenant | None = result.scalar_one_or_none()
    return tenant


async def _connection_for(session: Any, user: TokenUser) -> SeekConnection | None:
    tenant = await _tenant_for(session, user)
    if tenant is None:
        return None
    result = await session.execute(
        select(SeekConnection).where(SeekConnection.tenant_id == tenant.id)
    )
    connection: SeekConnection | None = result.scalar_one_or_none()
    return connection


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


@router.get("/settings", response_class=HTMLResponse)
async def seek_settings(request: Request, session: DbSession, user: SeekUser) -> Response:
    """The connection form. Shows configured-or-not, never the key."""
    connection = await _connection_for(session, user)
    return render_template(
        request,
        "seek_settings.html",
        {
            "user": user,
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
    """Verify the connection against SEEK itself, then store it.

    Verified by calling ``default_project_id`` before saving, so a wrong key or
    unreachable instance is rejected here — not discovered at the first sync.
    """
    from metaseed.seek import client_from_settings

    url = url.strip().rstrip("/")
    api_key = api_key.strip()
    error = None
    try:
        project = await run_in_threadpool(
            lambda: client_from_settings({"url": url, "api_key": api_key}).default_project_id()
        )
    except Exception as exc:
        logger.info("SEEK connection verification failed: %s", exc)
        error = (
            "SEEK did not accept this connection — check the URL is reachable "
            "from the server and the API key is valid."
        )
        project = None

    message = None
    if error is None:
        tenant = await _tenant_for(session, user)
        if tenant is None:  # pragma: no cover - a signed-in user has a tenant
            return _panel(request, error="No tenant for this account.")
        connection = await _connection_for(session, user)
        if connection is None:
            connection = SeekConnection(
                tenant_id=tenant.id,
                url=url,
                api_key_encrypted=encrypt_secret(api_key),
            )
            session.add(connection)
        else:
            connection.url = url
            connection.api_key_encrypted = encrypt_secret(api_key)
        await session.commit()
        message = f"Connected — default project id {project}."

    return render_template(
        request,
        "seek_settings.html",
        {
            "user": user,
            "configured_url": url if error is None else None,
            "message": message,
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
    connection = await _connection_for(session, user)
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
