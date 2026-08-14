"""Dataset editor, tree, overview, validation, graph, and export routes."""

import logging
from typing import Annotated, Any

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from metaseed import SkippedNode
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metaseed_hub.models import (
    Dataset,
    DatasetMember,
    Tenant,
)
from metaseed_hub.ui.dependencies import (
    CurrentUser,
    DbSession,
    get_dataset_for_editor,
    get_dataset_for_user,
)
from metaseed_hub.ui.helpers import (
    add_entities_in_order,
    ensure_dataset_facade,
    ensure_dataset_facade_for_write,
    get_tree_data_from_nodes,
    group_entities_by_type,
    parse_workbook_sheets,
    read_upload_capped,
    save_dataset_state,
)
from metaseed_hub.ui.helpers.load_report import skipped_node_message
from metaseed_hub.ui.helpers.spec_hash import spec_drift_message
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.routes.seek import profile_supports_seek
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error
from metaseed_hub.ui.services.seek_connection import connection_for_user

from ._router import router

logger = logging.getLogger("metaseed_hub")


async def _build_dataset_context(
    dataset: Dataset,
    session: Any,
) -> dict[str, Any]:
    """Build common context for dataset views.

    Args:
        dataset: Dataset model.
        session: Database session for loading spec drafts.

    Returns:
        Dictionary with state, tree_data, and entity_descriptions.
    """
    state = await ensure_dataset_facade(dataset, session)
    tree_data = get_tree_data_from_nodes(state)

    # Get descriptions for entity types
    entity_descriptions: dict[str, str] = {}

    try:
        facade = state.get_or_create_facade()
        for entity_name in facade.entities:
            helper = getattr(facade, entity_name, None)
            if helper:
                entity_descriptions[entity_name] = helper.description or ""

    except Exception as e:
        # Log but don't crash
        logger.warning(f"Failed to load facade for dataset {dataset.id}: {e}")

    return {
        "state": state,
        "tree_data": tree_data,
        "entity_descriptions": entity_descriptions,
    }


def _dcat_documents(state: Any, identifier: str) -> dict[str, str] | None:
    """The dataset's DCAT card as {"dcat.jsonld": ..., "dcat.ttl": ...}.

    Harvesters read metadata from the landing page (embedded JSON-LD,
    content negotiation), never from a downloadable file — so the page and
    the negotiated representations serve the same card the export writes.
    None when the dataset derives no card (no entities).
    """
    from metaseed import MetaseedClient
    from metaseed.dcat.export import to_dcat

    client = MetaseedClient.from_facade(state.get_or_create_facade())
    try:
        return to_dcat(client, identifier=identifier) or None
    except Exception:  # a card failure must not take the page down
        logger.exception("DCAT card for %s failed", identifier)
        return None


@router.get("/{dataset_id}", response_class=HTMLResponse)
async def dataset_editor(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Dataset editor - wraps metaseed UI with hub chrome.

    Also the dataset's DCAT surface: ``Accept: application/ld+json`` or
    ``text/turtle`` answers the catalog record itself, the HTML page embeds
    the same record as JSON-LD, and every response carries a
    ``rel="describedby"`` Link — the three signals FAIR harvesters read.
    """
    # Verify user has access to this dataset
    dataset = await get_dataset_for_user(dataset_id, session, user)

    # Load tenant for breadcrumb
    tenant = await session.get(Tenant, dataset.tenant_id)

    # Load members with user info
    members_result = await session.execute(
        select(DatasetMember)
        .where(DatasetMember.dataset_id == dataset_id)
        .options(selectinload(DatasetMember.user))
    )
    members = list(members_result.scalars().all())

    # Build common dataset context
    try:
        ctx = await _build_dataset_context(dataset, session)
        root_types = ctx["state"].get_root_entity_types()
        dcat_documents = _dcat_documents(ctx["state"], dataset.name)

        # Content negotiation: a harvester asking for RDF gets the record
        # itself, not an HTML page it cannot read.
        accept = request.headers.get("accept", "")
        describedby = f'<{request.url.path}>; rel="describedby"; type="application/ld+json"'
        if dcat_documents and "application/ld+json" in accept:
            return Response(
                content=dcat_documents["dcat.jsonld"],
                media_type="application/ld+json",
                headers={"Link": describedby},
            )
        if dcat_documents and "text/turtle" in accept:
            return Response(
                content=dcat_documents["dcat.ttl"],
                media_type="text/turtle",
                headers={"Link": describedby},
            )
    except Exception as e:
        # Profile doesn't exist or is invalid - show error page
        logger.warning(f"Failed to load dataset {dataset_id}: {e}")
        return render_template(
            request=request,
            name="dataset.html",
            context={
                "user": user,
                "dataset": dataset,
                "tenant": tenant,
                "members": members,
                "state": None,
                "root_types": [],
                "tree_data": [],
                "entity_descriptions": {},
                "nav_active": "home",
                "error": (
                    f"Could not load profile '{dataset.profile}' v{dataset.version}. "
                    "The profile may not exist or may be invalid."
                ),
            },
        )

    response = render_template(
        request=request,
        name="dataset.html",
        context={
            "user": user,
            "dataset": dataset,
            "tenant": tenant,
            "members": members,
            "state": ctx["state"],
            "root_types": root_types,
            "tree_data": ctx["tree_data"],
            "entity_descriptions": ctx["entity_descriptions"],
            "seek_supported": profile_supports_seek(dataset.profile, dataset.version),
            "connection": await connection_for_user(session, user),
            "export_options": _adapter_export_options(dataset.profile),
            # Offered only while the dataset is empty: the importer replaces the
            # whole tree, so it is a way to start, not a way to merge.
            "import_option": (
                _source_import_option(dataset.profile) if not ctx["tree_data"] else None
            ),
            "nav_active": "home",
            "dcat_jsonld": dcat_documents["dcat.jsonld"] if dcat_documents else None,
        },
    )
    response.headers["Link"] = describedby
    return response


@router.get("/{dataset_id}/tree", response_class=HTMLResponse)
async def dataset_tree(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return entity tree for a dataset."""
    # Verify user has access to this dataset
    dataset = await get_dataset_for_user(dataset_id, session, user)

    state = await ensure_dataset_facade(dataset, session)
    tree_data = get_tree_data_from_nodes(state)

    return render_template(
        request=request,
        name="partials/entity_tree.html",
        context={
            "tree_data": tree_data,
            "dataset_id": dataset_id,
        },
    )


@router.get("/{dataset_id}/root-buttons", response_class=HTMLResponse)
async def dataset_root_buttons(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return root entity type buttons for the sidebar."""
    dataset = await get_dataset_for_user(dataset_id, session, user)

    state = await ensure_dataset_facade(dataset, session)
    root_types = state.get_root_entity_types()
    tree_data = get_tree_data_from_nodes(state)
    existing_root_types = [item["entity_type"] for item in tree_data]

    return render_template(
        request=request,
        name="partials/root_buttons.html",
        context={
            "dataset_id": dataset_id,
            "root_types": root_types,
            "existing_root_types": existing_root_types,
        },
    )


@router.get("/{dataset_id}/overview", response_class=HTMLResponse)
async def dataset_overview(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return the overview panel with version history, comments, and sharing."""
    dataset = await get_dataset_for_user(dataset_id, session, user)

    # Load members
    members_result = await session.execute(
        select(DatasetMember)
        .where(DatasetMember.dataset_id == dataset_id)
        .options(selectinload(DatasetMember.user))
    )
    members = list(members_result.scalars().all())

    # Check if there's any tree data
    state = await ensure_dataset_facade(dataset, session)
    tree_data = get_tree_data_from_nodes(state)

    return render_template(
        request=request,
        name="partials/dataset_overview.html",
        context={
            "dataset": dataset,
            "members": members,
            "tree_data": tree_data,
        },
    )


@router.post("/{dataset_id}/validate", response_class=HTMLResponse)
async def dataset_validate(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> HTMLResponse:
    """Validate all entities in the dataset against their schemas."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Verify user has access to this dataset
    dataset = await get_dataset_for_user(dataset_id, session, user)

    # Collected during the load: a stored node that did not load is missing from
    # everything below, including the counts, so the panel has to say so itself.
    skipped: list[SkippedNode] = []
    state = await ensure_dataset_facade(dataset, session, on_skip=skipped.append)
    facade = state.get_or_create_facade()

    errors = _collect_validation_errors(state, facade)
    drift = await spec_drift_message(session, dataset)
    return HTMLResponse(
        _render_validation_results(
            dataset_id,
            state,
            errors,
            drift=drift,
            unloadable=[skipped_node_message(skip) for skip in skipped],
        )
    )


def _collect_validation_errors(state: Any, facade: Any) -> list[dict[str, Any]]:
    """Re-validate every entity node and return one error record per failing node."""
    from pydantic import ValidationError

    errors: list[dict[str, Any]] = []
    for node_id, node in state.nodes_by_id.items():
        try:
            helper = getattr(facade, node.entity_type)
            data = node.instance.model_dump(exclude_none=True) if node.instance else {}
            # Re-validate by recreating - Pydantic validation runs here
            helper.create(**data)
        except ValidationError as e:
            errors.append(
                {
                    "node_id": node_id,
                    "entity_type": node.entity_type,
                    "label": node.label,
                    "errors": [
                        {"field": ".".join(str(x) for x in err["loc"]), "message": err["msg"]}
                        for err in e.errors()
                    ],
                }
            )
        except AttributeError:
            errors.append(
                {
                    "node_id": node_id,
                    "entity_type": node.entity_type,
                    "label": node.label,
                    "errors": [
                        {"field": "", "message": f"Unknown entity type: {node.entity_type}"}
                    ],
                }
            )
        except Exception as e:
            errors.append(
                {
                    "node_id": node_id,
                    "entity_type": node.entity_type,
                    "label": node.label,
                    "errors": [{"field": "", "message": str(e)}],
                }
            )
    return errors


def _render_validation_results(
    dataset_id: str,
    state: Any,
    errors: list[dict[str, Any]],
    drift: str | None = None,
    unloadable: list[str] | None = None,
) -> str:
    """Render the validation summary, per-type breakdown, and error list as HTML.

    Args:
        dataset_id: The dataset being reported on, for the entity links.
        state: The loaded AppState, for the per-type counts.
        errors: One record per entity that failed validation.
        drift: A message saying the specification changed since the dataset was
            written, or None. Shown above the summary because it usually
            explains the issues below it, but kept out of the counts: it is
            provenance, not an entity that failed.
        unloadable: One message per stored node that did not load. Shown first
            and kept out of the counts, which can only count what loaded --
            that is exactly why these need saying separately.

    Returns:
        The panel's HTML.
    """
    import html as html_module

    # Count entities by type
    entity_counts: dict[str, int] = {}
    for node in state.nodes_by_id.values():
        entity_counts[node.entity_type] = entity_counts.get(node.entity_type, 0) + 1

    total = len(state.nodes_by_id)
    valid_count = total - len(errors)

    html = '<div class="validation-results">'

    if unloadable:
        html += '<div class="validation-unloadable"><strong>Entities that did not load</strong>'
        for message in unloadable:
            html += f"<p>{html_module.escape(message)}</p>"
        html += "</div>"

    if drift:
        html += (
            '<div class="validation-drift">'
            "<strong>Specification drift</strong>"
            f"<p>{html_module.escape(drift)}</p>"
            "</div>"
        )

    # Summary section
    if not errors:
        html += f"""
        <div class="validation-summary validation-success">
            <div class="validation-icon">&#10003;</div>
            <div class="validation-summary-text">
                <strong>All {total} entities are valid</strong>
                <p>Your dataset passes all validation checks.</p>
            </div>
        </div>
        """
    else:
        html += f"""
        <div class="validation-summary validation-error">
            <div class="validation-icon">&#10007;</div>
            <div class="validation-summary-text">
                <strong>{len(errors)} of {total} entities have issues</strong>
                <p>{valid_count} valid. Fix issues below.</p>
            </div>
        </div>
        """

    # Entity type breakdown
    html += '<div class="validation-breakdown"><h4>Entity Summary</h4><div class="breakdown-grid">'
    for entity_type, count in sorted(entity_counts.items()):
        error_count = sum(1 for e in errors if e["entity_type"] == entity_type)
        status_class = "valid" if error_count == 0 else "invalid"
        html += f"""
        <div class="breakdown-item {status_class}">
            <span class="breakdown-type">{html_module.escape(entity_type)}</span>
            <span class="breakdown-count">{count - error_count}/{count} valid</span>
        </div>
        """
    html += "</div></div>"

    # Detailed errors
    if errors:
        html += '<div class="validation-errors"><h4>Issues to Fix</h4>'
        for err in errors:
            html += f"""
            <div class="validation-error-item">
                <div class="validation-entity">
                    <span class="entity-type-badge">{html_module.escape(err["entity_type"])}</span>
                    <a href="#" class="entity-link"
                       hx-get="/hub/datasets/{dataset_id}/entity/{err["node_id"]}"
                       hx-target="#editor"
                       hx-swap="innerHTML">{html_module.escape(err["label"] or "")}</a>
                </div>
                <ul class="validation-error-list">
            """
            for field_err in err["errors"]:
                field = field_err["field"] or "(general)"
                html += (
                    f"<li><code>{html_module.escape(field)}</code>: "
                    f"{html_module.escape(field_err['message'])}</li>"
                )
            html += "</ul></div>"
        html += "</div>"

    html += "</div>"
    return html


@router.get("/{dataset_id}/graph", response_class=HTMLResponse)
async def dataset_graph(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
    node_id: str | None = None,
) -> Response:
    """Graph visualization of dataset entities.

    Args:
        node_id: Optional. If provided, only show this entity and its descendants.
    """
    # Verify user has access to this dataset
    dataset = await get_dataset_for_user(dataset_id, session, user)

    return render_template(
        request=request,
        name="graph.html",
        context={
            "user": user,
            "dataset": dataset,
            "node_id": node_id,
            "nav_active": "home",
        },
    )


def _filter_graph_to_subtree(graph_data: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Restrict graph data to ``node_id`` and its descendants.

    Descendants are found by following edges outward from ``node_id``. An
    unknown ``node_id`` returns the graph unchanged, so a stale link renders
    the whole graph rather than an empty canvas.

    Args:
        graph_data: ``build_graph`` output with ``nodes`` and ``edges`` lists.
        node_id: Root of the subtree to keep.

    Returns:
        Graph data with nodes and edges outside the subtree removed.
    """
    if node_id not in {n["id"] for n in graph_data["nodes"]}:
        return graph_data

    children: dict[str, list[str]] = {}
    for edge in graph_data["edges"]:
        children.setdefault(edge["from"], []).append(edge["to"])

    keep = {node_id}
    queue = [node_id]
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in keep:
                keep.add(child)
                queue.append(child)

    return {
        **graph_data,
        "nodes": [n for n in graph_data["nodes"] if n["id"] in keep],
        "edges": [e for e in graph_data["edges"] if e["from"] in keep and e["to"] in keep],
    }


@router.get("/{dataset_id}/api/graph")
async def dataset_graph_api(
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
    node_id: str | None = None,
) -> Response:
    """Return graph data for visualization (JSON API).

    Builds nodes and edges from the facade via the hub's graph service.

    Args:
        node_id: Optional. If provided, only include this node and its descendants.
    """
    from fastapi.responses import JSONResponse

    from metaseed_hub.ui.services.graph import build_graph

    # Verify user has access to this dataset
    dataset = await get_dataset_for_user(dataset_id, session, user)

    state = await ensure_dataset_facade(dataset, session)

    graph_data = build_graph(state.get_or_create_facade())
    if node_id:
        graph_data = _filter_graph_to_subtree(graph_data, node_id)

    return JSONResponse(content=graph_data)


@router.get("/{dataset_id}/export")
async def dataset_export(
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Export dataset data to Excel file.

    Uses the hub's export service to generate an Excel workbook containing all
    entities in the dataset.
    """
    from fastapi.responses import StreamingResponse

    from metaseed_hub.ui.services.export import export_to_bytes, generate_filename

    dataset = await get_dataset_for_user(dataset_id, session, user)
    state = await ensure_dataset_facade(dataset, session)
    facade = state.get_or_create_facade()

    excel_bytes = export_to_bytes(facade)
    filename = generate_filename(facade)

    # If no data, use dataset name for filename
    if not filename or filename == "export.xlsx":
        filename = f"{dataset.name.replace(' ', '_')}.xlsx"

    return StreamingResponse(
        excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _source_import_option(profile: str) -> dict[str, str] | None:
    """The import control a profile offers, from metaseed's registry, or None.

    Carries the action's own wording for the single value it takes, since an
    accession and a BrAPI server URL are not interchangeable and the hub should
    not hold a per-repository phrasebook.
    """
    from metaseed_hub.ui.routes.dataset.crud import source_import_action

    action = source_import_action(profile)
    if action is None:
        return None
    return {
        "label": action.label,
        "input_label": action.input_label,
        "placeholder": action.input_placeholder,
    }


def _adapter_export_options(profile: str) -> list[dict[str, str]]:
    """[{key, label}] adapter exports offered for a profile, from the registry.

    The registry is the whole answer: adapters are plugins available to every
    signed-in user, like the metaseed UI offers them. The per-group
    FeatureGrant filter that sat here hid EVERY export from every user,
    because nothing ever wrote a grant row.
    """
    from metaseed import adapters

    return [
        {"key": action.key, "label": action.label}
        for action in adapters.actions_for_profile(profile, kind="export")
    ]


@router.get("/{dataset_id}/export/adapter/{fmt}")
async def dataset_export_adapter(
    dataset_id: str,
    fmt: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Export the dataset via a metaseed integration adapter, as a zip of files.

    Formats are declared in metaseed's adapter registry; a new exporter appears
    here by declaring itself upstream, not by editing this route.
    """
    import zipfile
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from metaseed import MetaseedClient, adapters

    action = adapters.find_action(fmt)
    if action is None or action.kind != "export":
        raise HTTPException(status_code=404, detail=f"Unknown export format: {fmt}")

    dataset = await get_dataset_for_user(dataset_id, session, user)

    # Gate on the same predicate that renders the buttons, so a hand-typed format
    # cannot run an exporter against a profile it was never meant for.
    if action not in adapters.actions_for_profile(dataset.profile, kind="export"):
        raise HTTPException(
            status_code=404,
            detail=f"{fmt} export is not available for the {dataset.profile} profile.",
        )

    state = await ensure_dataset_facade(dataset, session)
    # from_facade, not __new__ plus a private attribute — the same fork the
    # metaseed review retired from metaseed's own routes.
    client = MetaseedClient.from_facade(state.get_or_create_facade())

    try:
        export_fn = action.resolve()
    except ModuleNotFoundError as exc:  # optional extra not installed
        raise HTTPException(
            status_code=400,
            detail=f"{fmt} export requires the matching metaseed extra.",
        ) from exc
    except (ImportError, AttributeError) as exc:
        raise HTTPException(status_code=500, detail=f"{fmt} export is misconfigured.") from exc

    try:
        files: dict[str, str] = export_fn(client)
    except Exception as exc:  # a plugin failure degrades to an error, not a 500 stack
        raise HTTPException(status_code=500, detail=f"{fmt} export failed: {exc}") from exc

    if not files:
        raise HTTPException(
            status_code=400,
            detail=f"Nothing to export for {fmt}: the dataset is empty or does "
            f"not match this format's expected structure.",
        )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            # Never let an exporter-supplied name escape the archive root.
            from pathlib import PurePosixPath

            archive.writestr(PurePosixPath(name).name, content)
    buffer.seek(0)

    stem = dataset.name.replace(" ", "_") or "dataset"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{stem}-{fmt}.zip"'},
    )


@router.post("/{dataset_id}/import")
async def dataset_import_into_existing(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> Response:
    """Import data from file into an existing dataset.

    Supports JSON, YAML, and Excel files. Adds entities to the existing dataset.
    """
    import html
    import json

    import yaml
    from metaseed.specs.loader import SpecLoader

    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    dataset = await get_dataset_for_editor(dataset_id, session, user)

    # Read file content (capped to avoid reading an unbounded upload into memory)
    try:
        content = await read_upload_capped(file)
    except HTTPException as exc:
        return HTMLResponse(
            f"<div class='notification error'>{exc.detail}</div>", status_code=exc.status_code
        )
    filename = file.filename or ""

    # The profile's root entity type: the default for payloads without a _type
    # marker (a profile name is never an entity type), and the first type
    # processed so children can attach to it.
    loader = SpecLoader(profile=dataset.profile)
    spec = loader.load_profile(dataset.version, dataset.profile)
    root_entity = spec.root_entity or "Investigation"

    # Parse based on file type
    entities_by_type: dict[str, list[dict[str, Any]]] = {}

    try:
        data: Any = None
        if filename.endswith((".yaml", ".yml")):
            data = yaml.safe_load(content.decode("utf-8"))
        elif filename.endswith(".json"):
            data = json.loads(content.decode("utf-8"))
        elif filename.endswith((".xlsx", ".xls")):
            entities_by_type = parse_workbook_sheets(content)
        else:
            return HTMLResponse(
                "<div class='notification error'>Unsupported file format</div>",
                status_code=400,
            )

        if isinstance(data, dict):
            if "entities" in data:
                entities_by_type = group_entities_by_type(data["entities"], root_entity)
            else:
                # Single entity - use root entity type
                entities_by_type[root_entity] = [data]

    except Exception as e:
        # Parse exceptions embed excerpts of the uploaded file, so the text must
        # be escaped before it lands in the HTMX swap target.
        logger.exception(f"Failed to parse import file: {e}")
        return HTMLResponse(
            f"<div class='notification error'>Parse error: {html.escape(str(e)[:200])}</div>",
            status_code=400,
        )

    # Get dataset state and add entities. The write-path loader refuses when a
    # stored node did not load: this route saves below, and saving a partial
    # load is what deletes the nodes that were skipped.
    try:
        state = await ensure_dataset_facade_for_write(dataset, session)
    except HTTPException as exc:
        return HTMLResponse(
            f"<div class='notification error'>{html.escape(str(exc.detail))}</div>",
            status_code=exc.status_code,
        )
    facade = state.get_or_create_facade()

    imported_count, errors = add_entities_in_order(state, facade, entities_by_type, root_entity)

    # Save to database with version history
    await save_dataset_state(session, dataset, state, user)

    if errors:
        msg = f"Imported {imported_count} entities with {len(errors)} errors"
        logger.warning(f"Import errors: {errors[:5]}")
    else:
        msg = f"Successfully imported {imported_count} entities"

    return HTMLResponse(f"<div class='notification success'>{msg}</div>")


# =============================================================================
# Member Management Routes
# =============================================================================
