"""Dataset create, import, delete, and example-loading routes."""

import copy
import logging
from json import JSONDecodeError
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from metaseed.adapters import Action  # lightweight by design: no plugin imports
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from metaseed_hub.models import (
    Dataset,
    Spec,
    SpecDraft,
    SpecDraftMember,
    SpecStatus,
)
from metaseed_hub.ui.dependencies import (
    CurrentUser,
    DbSession,
    ensure_tenant_and_user,
    get_dataset_for_editor,
    require_dataset_owner,
)
from metaseed_hub.ui.helpers import (
    add_entities_in_order,
    add_entity_node,
    create_nested_nodes,
    ensure_dataset_facade,
    ensure_dataset_facade_for_write,
    group_entities_by_type,
    parse_workbook_sheets,
    read_upload_capped,
    save_dataset_state,
)
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

from ._router import router

if TYPE_CHECKING:
    from metaseed import MetaseedClient

    from metaseed_hub.auth import TokenUser

logger = logging.getLogger("metaseed_hub")


def _no_example_message(profile: str, version: str, *, found: bool) -> str:
    """Why no example could be loaded, with the profile and version escaped.

    Both are stored per dataset and reach this HTML fragment, which htmx swaps
    into the page — so they are content, never markup.
    """
    from html import escape

    what = "example file found for" if found else "example available for"
    return f"No {what} {escape(str(profile))} v{escape(str(version))}"


@router.get("/new", response_class=HTMLResponse)
async def dataset_new(
    request: Request,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Return dataset creation form."""
    from metaseed.specs.loader import SpecLoader

    # Get or create tenant and user
    tenant, db_user = await ensure_tenant_and_user(session, user)

    # Get available profiles and versions from metaseed
    import metaseed

    loader = SpecLoader()
    examples_dir = Path(metaseed.__file__).parent / "examples"
    profiles_data = []
    for profile_name in loader.list_profiles():
        versions = loader.list_versions(profile_name)

        # Sort versions in descending order (newest first)
        def version_key(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)

        versions = sorted(versions, key=version_key, reverse=True)
        # Get profile metadata from latest version
        display_name = profile_name
        description = ""
        root_entity = "Investigation"
        if versions:
            try:
                spec = loader.load_profile(versions[0], profile_name)
                display_name = spec.display_name or profile_name
                description = spec.description or ""
                root_entity = spec.root_entity or "Investigation"
            except Exception as e:
                # Fall back to defaults if a profile's metadata won't load, but
                # log it so the failure is not invisible.
                logger.debug(f"Could not load metadata for profile {profile_name}: {e}")

        # Check if examples exist for this profile (check latest version)
        has_example = False
        if versions:
            example_path = examples_dir / profile_name / versions[0]
            has_example = example_path.exists() and any(example_path.glob("*.yaml"))

        profiles_data.append(
            {
                "name": profile_name,
                "display_name": display_name,
                "description": description,
                "root_entity": root_entity,
                "versions": versions,
                "latest_version": versions[0] if versions else "",
                "source": "builtin",
                "has_example": has_example,
            }
        )

    # Get spec drafts from this tenant
    drafts_result = await session.execute(select(SpecDraft).where(SpecDraft.tenant_id == tenant.id))
    owned_drafts = list(drafts_result.scalars().all())

    # Also get specs shared with this user via SpecDraftMember
    shared_drafts: list[SpecDraft] = []
    shared_result = await session.execute(
        select(SpecDraft)
        .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
        .where(SpecDraftMember.user_id == db_user.id)
    )
    shared_drafts = list(shared_result.scalars().all())

    # Combine and deduplicate
    seen_ids: set[str] = set()
    drafts: list[SpecDraft] = []
    for draft in owned_drafts + shared_drafts:
        if draft.id not in seen_ids:
            seen_ids.add(draft.id)
            drafts.append(draft)

    for draft in drafts:
        if draft.name:
            spec_data = draft.spec_data or {}
            profiles_data.append(
                {
                    "name": f"draft:{draft.id}",
                    "display_name": f"{draft.name} (Draft)",
                    "description": spec_data.get("description", ""),
                    "root_entity": spec_data.get("root_entity", "Investigation"),
                    "versions": [draft.version],
                    "latest_version": draft.version,
                    "source": "draft",
                }
            )

    # Every published spec, from any account, offered as a starting point:
    # publishing is what makes a specification available to other people.
    specs_result = await session.execute(
        select(Spec)
        .options(selectinload(Spec.created_by))
        .where(
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
        )
        .order_by(Spec.updated_at.desc())
    )
    user_specs = list(specs_result.scalars().all())

    return render_template(
        request=request,
        name="dataset_new.html",
        context={
            "user": user,
            "tenant_id": tenant.id,
            "profiles": profiles_data,
            "user_specs": user_specs,
            "nav_active": "home",
        },
    )


@router.post("/import")
async def dataset_import(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()],
    profile: Annotated[str | None, Form()] = None,
    version: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
) -> RedirectResponse:
    """Import a dataset from an uploaded file (JSON, YAML, or Excel)."""
    import json

    import yaml
    from metaseed.specs.loader import SpecLoader

    from metaseed_hub.ui.helpers import validate_csrf_token

    if not validate_csrf_token(request, csrf_token):
        return RedirectResponse("/hub/?error=csrf_validation_failed", status_code=302)

    # Get or create tenant and user
    tenant, db_user = await ensure_tenant_and_user(session, user)

    # Read file content (capped to avoid reading an unbounded upload into memory)
    try:
        content = await read_upload_capped(file)
    except HTTPException:
        return RedirectResponse("/hub/datasets/new?error=file_too_large", status_code=302)
    filename = file.filename or ""

    # Parse based on file type. Keep the form-supplied profile/version; the
    # detection below only fills them in when the user did not select them.
    data = None

    try:
        if filename.endswith((".yaml", ".yml")):
            data = yaml.safe_load(content.decode("utf-8"))
        elif filename.endswith(".json"):
            data = json.loads(content.decode("utf-8"))
        elif filename.endswith((".xlsx", ".xls")):
            # Excel import - each sheet name = entity type, headers = field names.
            entities_by_type = parse_workbook_sheets(content)

            # Store entities for later processing
            data = {"_entities_by_type": entities_by_type}

            # Use first entity of first sheet for root if available
            if entities_by_type:
                first_type = list(entities_by_type.keys())[0]
                if entities_by_type[first_type]:
                    data.update(entities_by_type[first_type][0])
        else:
            return RedirectResponse("/hub/datasets/new?error=unsupported_format", status_code=302)

        if not data:
            return RedirectResponse("/hub/datasets/new?error=empty_file", status_code=302)

        # Use provided profile/version, or try to detect from data
        if not profile and isinstance(data, dict):
            profile = data.get("profile") or data.get("_profile")
        if not version and isinstance(data, dict):
            version = data.get("version") or data.get("_version")

        # Default to miappe if not detected
        if not profile:
            profile = "miappe"
        if not version:
            loader = SpecLoader()
            versions = loader.list_versions(profile)
            version = versions[0] if versions else "1.1"

    except Exception as e:
        logger.exception(f"Failed to parse import file: {e}")
        return RedirectResponse("/hub/datasets/new?error=parse_error", status_code=302)

    # Create dataset
    dataset = Dataset(
        tenant_id=tenant.id,
        name=name,
        profile=profile,
        version=version,
        data={},
    )
    session.add(dataset)
    try:
        await session.commit()
    except IntegrityError:
        # A tenant may not have two datasets with the same name; surface this as a
        # redirect rather than an unhandled 500.
        await session.rollback()
        return RedirectResponse("/hub/datasets/new?error=duplicate_name", status_code=302)
    await session.refresh(dataset)

    # Try to import entities from data
    try:
        loader = SpecLoader(profile=profile)
        spec = loader.load_profile(version, profile)
        root_entity = spec.root_entity or "Investigation"

        # The dataset was just created empty, so this yields a fresh state whose
        # facade is the authoritative store for the imported entities.
        state = await ensure_dataset_facade(dataset, session)
        facade = state.get_or_create_facade()

        # Handle different data structures
        entities_by_type = data.get("_entities_by_type", {}) if isinstance(data, dict) else {}
        entities_data = data.get("entities", []) if isinstance(data, dict) else []

        if entities_by_type:
            # Excel import - one sheet per entity type, root entity first.
            _, import_errors = add_entities_in_order(state, facade, entities_by_type, root_entity)
            if import_errors:
                logger.warning(f"Import errors: {import_errors[:5]}")
        elif entities_data:
            # Export format ({"entities": [...]} as produced by serialize/export):
            # recreate each entity by its _type marker so an export round-trips.
            grouped = group_entities_by_type(entities_data, root_entity)
            _, import_errors = add_entities_in_order(state, facade, grouped, root_entity)
            if import_errors:
                logger.warning(f"Import errors: {import_errors[:5]}")
        elif isinstance(data, dict):
            # Try to use the data directly as root entity
            # Filter out metadata fields
            entity_data = {
                k: v
                for k, v in data.items()
                if not k.startswith("_") and k not in ("profile", "version")
            }
            if entity_data:
                node = add_entity_node(state, root_entity, entity_data)
                create_nested_nodes(state, facade, node, root_entity, copy.deepcopy(entity_data))

        if state.editing_node_id is None and state.entity_tree:
            state.editing_node_id = state.entity_tree[0].id

        # Save to database with version history
        await save_dataset_state(session, dataset, state, user)

    except Exception as e:
        logger.warning(f"Could not import entities, dataset created empty: {e}")

    return RedirectResponse(f"/hub/datasets/{dataset.id}", status_code=303)


def source_import_action(profile: str) -> Action | None:
    """The registry's import-menu action for ``profile``, or None.

    metaseed declares one per repository it can pull from (ENA, PRIDE,
    MetaboLights, BrAPI). Resolving through the registry rather than naming the
    importers here means a new one reaches the hub by being declared upstream.
    """
    from metaseed import adapters

    return next(
        iter(adapters.actions_for_profile(profile, kind="import", surface="import-menu")),
        None,
    )


def run_source_import(profile: str, value: str) -> "MetaseedClient":
    """Import ``value`` through the importer registered for ``profile``.

    Every import action takes a single string, though its meaning varies by
    repository: an accession for the archives, a server URL for BrAPI. The
    action's ``input_label`` is what tells the user which to supply.

    Raises:
        LookupError: If no importer is registered for ``profile``.
    """
    action = source_import_action(profile)
    if action is None:
        raise LookupError(f"No source importer for profile '{profile}'")
    client: MetaseedClient = action.resolve()(value)
    return client


async def create_dataset_from_accession(
    session: DbSession,
    tenant_id: str,
    name: str,
    profile: str,
    accession: str,
    user: "TokenUser | None" = None,
) -> Dataset:
    """Import a public dataset from a source database into a new dataset.

    Reuses metaseed's adapter registry: the importer for ``profile`` (ENA, PRIDE,
    MetaboLights) is resolved from ``actions_for_profile(kind="import")`` and
    invoked with ``accession`` -- the same importer the CLI and MCP use, none
    reimplemented here. The imported entities are loaded into the new dataset by
    swapping in the importer's facade.

    Args:
        session: Database session.
        tenant_id: Tenant that will own the dataset.
        name: Name for the new dataset.
        profile: Profile whose registered importer resolves the accession.
        accession: Identifier to import.
        user: The acting user, recorded as the created version's author.

    Raises:
        LookupError: If no accession importer is registered for ``profile``.
        ValueError: If the importer resolved ``accession`` to nothing.
    """
    client = run_source_import(profile, accession)
    if not client.serialize().get("entities"):
        # Creating an empty dataset named after an accession that resolved to
        # nothing leaves the user to discover the failure themselves. Distinct
        # from LookupError above so the caller does not blame a missing importer.
        raise ValueError(f"Nothing was found for '{accession}'")

    dataset = Dataset(
        tenant_id=tenant_id,
        name=name,
        profile=client.profile,
        version=client.version,
        data={},
    )
    session.add(dataset)
    await session.flush()

    state = await ensure_dataset_facade(dataset, session)
    state.profile = client.profile
    state.version = client.version
    # Swap in the importer's facade and rebuild caches; do not reset() (it clears).
    state.facade = client.facade
    state.invalidate_cache()
    await save_dataset_state(session, dataset, state, user)
    return dataset


def _import_failure_message(exc: Exception, value: str) -> str:
    """Explain an import failure in terms the user can act on.

    The archives fail in a handful of distinguishable ways, and each points at a
    different mistake: a 404 usually means the address is wrong rather than the
    record missing, and HTML where JSON was expected means the URL is not an API
    endpoint at all.
    """
    import html

    detail = html.escape(str(exc)[:200])
    safe_value = html.escape(value)
    status = getattr(getattr(exc, "response", None), "status_code", None)

    if isinstance(exc, JSONDecodeError) or "JSONDecode" in type(exc).__name__:
        return (
            f"'{safe_value}' did not return JSON, so it is probably not an API "
            "endpoint. For a BrAPI server the address must end in the API path, "
            "for example <code>https://server.example.org/brapi/v2</code>."
        )
    if status == 404:
        return (
            f"Nothing at '{safe_value}' (404). For a BrAPI server, check the "
            "address ends in <code>/brapi/v2</code>; for an accession, check it "
            "exists in the archive."
        )
    if status in (401, 403):
        return (
            f"'{safe_value}' refused access ({status}). It may be a private "
            "record or a server that requires a token."
        )
    return f"Import failed: {detail}"


@router.post("/{dataset_id}/import-source", response_class=HTMLResponse)
async def dataset_import_source(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
    value: Annotated[str, Form()],
) -> Response:
    """Fill an empty dataset from the source database its profile can import.

    Offered only while the dataset has no entities, and refused server-side in
    the same case: the importer replaces the whole entity tree, so running it
    over authored content would discard that content with no undo.
    """
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    dataset = await get_dataset_for_editor(dataset_id, session, user)
    state = await ensure_dataset_facade_for_write(dataset, session)
    if state.nodes_by_id:
        return HTMLResponse(
            "<div class='notification error'>This dataset already has entities. "
            "Importing would replace them, so it is only offered while the dataset "
            "is empty.</div>",
            status_code=400,
        )

    try:
        client = run_source_import(dataset.profile, value.strip())
    except LookupError:
        return HTMLResponse(
            f"<div class='notification error'>No importer is registered for the "
            f"{dataset.profile} profile.</div>",
            status_code=404,
        )
    except Exception as exc:
        # A bad accession or an archive outage must not 500 the page — but the
        # message has to say what went wrong. "Check the identifier" sent people
        # hunting for a bad accession when the real answer was a URL missing its
        # /brapi/v2 suffix.
        logger.exception("Source import failed for %s:%s", dataset.profile, value)
        return HTMLResponse(
            f"<div class='notification error'>{_import_failure_message(exc, value)}</div>",
            status_code=502,
        )

    # An archive that resolves nothing returns an empty client rather than
    # raising — a mistyped accession looks exactly like a successful import
    # unless this is checked, which is how it was reported.
    if not client.serialize().get("entities"):
        return HTMLResponse(
            f"<div class='notification error'>Nothing was found for "
            f"'{value.strip()}'. Check the identifier and try again.</div>",
            status_code=404,
        )

    state.facade = client.facade
    state.invalidate_cache()
    await save_dataset_state(session, dataset, state, user)

    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/datasets/{dataset_id}"
    return response


@router.post("/import-accession")
async def dataset_import_accession(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    profile: Annotated[str, Form()],
    accession: Annotated[str, Form()],
    name: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
) -> RedirectResponse:
    """Import a public dataset from a source database (ENA/PRIDE/MetaboLights)."""
    from metaseed_hub.ui.helpers import validate_csrf_token

    if not validate_csrf_token(request, csrf_token):
        return RedirectResponse("/hub/?error=csrf_validation_failed", status_code=302)

    tenant, _ = await ensure_tenant_and_user(session, user)
    try:
        dataset = await create_dataset_from_accession(
            session, tenant.id, name.strip(), profile, accession.strip(), user
        )
    except LookupError:
        return RedirectResponse("/hub/datasets/new?error=no_importer", status_code=302)
    except ValueError:
        # The importer ran but the accession resolved to nothing -- a typo, not
        # a missing importer.
        return RedirectResponse("/hub/datasets/new?error=import_empty", status_code=302)
    except IntegrityError:
        await session.rollback()
        return RedirectResponse("/hub/datasets/new?error=duplicate_name", status_code=302)
    except Exception:
        # A failed fetch (bad accession, database down) must not 500 the page.
        logger.exception("Accession import failed for %s:%s", profile, accession)
        return RedirectResponse("/hub/datasets/new?error=import_failed", status_code=302)

    await session.commit()
    return RedirectResponse(f"/hub/datasets/{dataset.id}", status_code=303)


@router.post("")
async def dataset_create(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    name: Annotated[str, Form()],
    profile: Annotated[str, Form()],
    version: Annotated[str, Form()],
    csrf_token: Annotated[str | None, Form(alias="_csrf_token")] = None,
    load_example: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Create a new dataset."""
    import metaseed
    import yaml
    from metaseed.specs.loader import SpecLoader

    from metaseed_hub.ui.helpers import validate_csrf_token

    if not validate_csrf_token(request, csrf_token):
        return RedirectResponse("/hub/?error=csrf_validation_failed", status_code=302)

    # Get or create tenant and user
    tenant, db_user = await ensure_tenant_and_user(session, user)

    # Check if using a draft spec
    spec_draft_id = None
    if profile.startswith("draft:"):
        spec_draft_id = profile.replace("draft:", "")
        # Resolve the draft only if it is accessible to this user, matching the
        # scoping in dataset_new: owned by the caller's tenant or shared via
        # SpecDraftMember. An unscoped lookup would let a user bind their dataset
        # to another tenant's draft spec.
        draft_result = await session.execute(
            select(SpecDraft)
            .outerjoin(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
            .where(
                SpecDraft.id == spec_draft_id,
                or_(
                    SpecDraft.tenant_id == tenant.id,
                    SpecDraftMember.user_id == db_user.id,
                ),
            )
        )
        draft = draft_result.scalars().first()
        if draft is None:
            return RedirectResponse("/hub/?error=draft_not_found", status_code=302)
        profile = draft.name.lower()  # Lowercase to match ProfileFacade behavior
        version = draft.version

    # A published specification, chosen from any account: publishing is what
    # makes one available to other people, so this is not scoped to the caller.
    # Only PUBLISHED and not withdrawn, so a draft stays unreachable by id.
    spec_id = None
    if profile.startswith("spec:"):
        spec_id = profile.replace("spec:", "")
        spec_result = await session.execute(
            select(Spec).where(
                Spec.id == spec_id,
                Spec.status == SpecStatus.PUBLISHED,
                Spec.deleted_at.is_(None),
            )
        )
        published = spec_result.scalar_one_or_none()
        if published is None:
            return RedirectResponse("/hub/?error=spec_not_found", status_code=302)
        profile = published.name.lower()  # Lowercase to match ProfileFacade
        version = published.version

    dataset = Dataset(
        tenant_id=tenant.id,
        name=name,
        profile=profile,
        version=version,
        spec_draft_id=spec_draft_id,
        spec_id=spec_id,
        data={},
    )
    session.add(dataset)
    try:
        await session.commit()
    except IntegrityError:
        # A tenant may not have two datasets with the same name.
        await session.rollback()
        return RedirectResponse("/hub/datasets/new?error=duplicate_name", status_code=302)
    await session.refresh(dataset)

    # Load example data if requested
    logger.info(
        f"dataset_create: load_example={load_example!r}, profile={profile}, version={version}"
    )
    if load_example == "true":
        examples_dir = Path(metaseed.__file__).parent / "examples"
        version_dir = examples_dir / profile / version
        yaml_files = list(version_dir.glob("*.yaml")) if version_dir.exists() else []

        if yaml_files:
            try:
                example_data = yaml.safe_load(yaml_files[0].read_text(encoding="utf-8"))
                # Deep copy to prevent Pydantic from modifying the original dict
                example_data_copy = copy.deepcopy(example_data)

                loader = SpecLoader(profile=profile)
                spec = loader.load_profile(version, profile)
                root_entity = spec.root_entity or "Investigation"

                # The dataset was just created empty, so this yields a fresh
                # state whose facade will hold the example entities.
                state = await ensure_dataset_facade(dataset, session)
                facade = state.get_or_create_facade()

                node = add_entity_node(state, root_entity, example_data)
                state.editing_node_id = node.id

                # Create nested child nodes from the unmodified copy
                create_nested_nodes(state, facade, node, root_entity, example_data_copy)

                # Save to database with version history
                await save_dataset_state(session, dataset, state, user)
            except Exception as e:
                logger.exception(f"Failed to load example data: {e}")

    return RedirectResponse(f"/hub/datasets/{dataset.id}", status_code=303)


@router.delete("/{dataset_id}", response_class=HTMLResponse)
async def dataset_delete(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Delete a dataset."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    try:
        # Verify user has access to this dataset
        dataset = await require_dataset_owner(dataset_id, session, user)
    except Exception as e:
        logger.error(f"Failed to get dataset {dataset_id}: {e}")
        response = Response(status_code=200)
        response.headers["HX-Trigger"] = (
            '{"showToast": {"message": "Dataset not found or access denied", "type": "error"}}'
        )
        return response

    try:
        # Soft-delete to match the repository and REST API, which mark
        # deleted_at and rely on the deleted_at IS NULL filter in every list
        # query. A hard delete here diverged from those paths and discarded the
        # related comments, notes, and version history irrecoverably.
        dataset.soft_delete()
        await session.commit()
    except Exception as e:
        logger.error(f"Failed to delete dataset {dataset_id}: {e}", exc_info=True)
        await session.rollback()
        error_msg = str(e).replace('"', '\\"')
        response = Response(status_code=200)
        response.headers["HX-Trigger"] = (
            f'{{"showToast": {{"message": "Delete failed: {error_msg}", "type": "error"}}}}'
        )
        return response

    # Always redirect to home after delete
    response = Response(
        content='<script>window.location.href="/hub/";</script>',
        status_code=200,
        media_type="text/html",
    )
    response.headers["HX-Redirect"] = "/hub/"
    return response


@router.post("/{dataset_id}/load-example", response_class=HTMLResponse)
async def dataset_load_example(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Load example data into a dataset from YAML files."""
    import metaseed
    import yaml
    from metaseed.specs.loader import SpecLoader

    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    # Verify user has access to this dataset
    dataset = await get_dataset_for_editor(dataset_id, session, user)

    # Find example YAML file
    examples_dir = Path(metaseed.__file__).parent / "examples"
    version_dir = examples_dir / dataset.profile / dataset.version

    if not version_dir.exists():
        return HTMLResponse(
            f"<div class='error'>"
            f"{_no_example_message(dataset.profile, dataset.version, found=False)}"
            f"</div>"
        )

    yaml_files = list(version_dir.glob("*.yaml"))
    if not yaml_files:
        return HTMLResponse(
            f"<div class='error'>"
            f"{_no_example_message(dataset.profile, dataset.version, found=True)}"
            f"</div>"
        )

    example_file = yaml_files[0]
    try:
        example_data = yaml.safe_load(example_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return HTMLResponse(f"<div class='error'>Error loading example: {e}</div>")

    # Deep copy to prevent Pydantic from modifying the original dict
    example_data_copy = copy.deepcopy(example_data)

    # Load spec to get root entity
    loader = SpecLoader(profile=dataset.profile)
    spec = loader.load_profile(dataset.version, dataset.profile)
    root_entity = spec.root_entity or "Investigation"

    # Load example into dataset state (append, don't replace). The facade must
    # hold the already-stored entities before the example is appended, or a
    # facade-based save would drop them.
    state = await ensure_dataset_facade_for_write(dataset, session)
    facade = state.get_or_create_facade()

    try:
        node = add_entity_node(state, root_entity, example_data)
        state.editing_node_id = node.id

        # Create nested child nodes from the unmodified copy
        create_nested_nodes(state, facade, node, root_entity, example_data_copy)

        # Save to database with version history
        await save_dataset_state(session, dataset, state, user)

    except Exception as e:
        # The traceback belongs in the log, not in the browser: it exposes
        # internal paths and code to the user without helping them.
        logger.exception(f"Failed to load example: {e}")
        return HTMLResponse(
            "<div class='notification error'>Could not load the example dataset. "
            "The error has been logged.</div>",
            status_code=500,
        )

    # Use HX-Redirect for HTMX to do a full page redirect
    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/datasets/{dataset_id}"
    return response
