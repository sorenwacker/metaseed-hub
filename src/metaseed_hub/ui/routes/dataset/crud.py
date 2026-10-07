"""Dataset create, import, delete, and example-loading routes."""

import copy
import json
import logging
from html import escape
from json import JSONDecodeError
from pathlib import Path
from typing import Annotated, Any

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from metaseed.specs.versioning import version_sort_key
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from metaseed_hub.audience import visible_specs
from metaseed_hub.models import (
    Spec,
    SpecDraft,
    SpecDraftMember,
    SpecStatus,
)
from metaseed_hub.repositories.datasets import DuplicateDatasetNameError, create_dataset
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
    ensure_dataset_facade_for_write,
    group_entities_by_type,
    parse_workbook_sheets,
    read_upload_capped,
    save_dataset_state,
)
from metaseed_hub.ui.helpers.spec_hash import dataset_profile_spec
from metaseed_hub.ui.metaseed_ui import AppState
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error
from metaseed_hub.ui.services.repository_import import run_source_import

from ._router import router
from .profile_choice import ProfileChoiceNotFoundError, resolve_profile_choice

# What the New Dataset form says for each ``?error=`` code a creation route
# redirects with. Without these the form came back empty and said nothing; the
# test suite fails when a route redirects with a code that has no entry here.
NEW_DATASET_ERRORS: dict[str, str] = {
    "duplicate_name": "You already have a dataset with that name. Choose another name.",
    "name_held_by_deleted": "A dataset you deleted still holds that name. Choose another name.",
    "file_too_large": "The file is too large to import.",
    "unsupported_format": "That file format is not supported. Use JSON, YAML or Excel.",
    "empty_file": "The file is empty.",
    "parse_error": "The file could not be read. Check that it is valid JSON, YAML or Excel.",
    "import_entities_failed": (
        "The file was read, but its entities could not be loaded under that profile and "
        "version. Check that they match the file. Nothing was created."
    ),
    "example_failed": "The example data could not be loaded for that profile. Nothing was created.",
    "draft_not_found": "That draft no longer exists in your account. Choose another profile.",
    "spec_not_found": (
        "That published specification is no longer available. Choose another profile."
    ),
}


def _duplicate_name_redirect(refused: DuplicateDatasetNameError) -> RedirectResponse:
    """Back to the form, saying which kind of clash it was."""
    code = "name_held_by_deleted" if refused.held_by_deleted else "duplicate_name"
    return RedirectResponse(f"/hub/datasets/new?error={code}", status_code=302)


logger = logging.getLogger("metaseed_hub")


def example_version_dir(examples_dir: Path, profile: str, version: str) -> Path | None:
    """The example directory for a profile version, if it is really one.

    `profile` and `version` are free text on the dataset, so joining them onto
    a base directory is not enough: `".."` walks out of the package and an
    absolute segment discards the base entirely. The result is resolved and
    required to sit under `examples_dir`.

    Args:
        examples_dir: Root of metaseed's packaged examples.
        profile: Profile name carried by the dataset.
        version: Profile version carried by the dataset.

    Returns:
        The resolved directory, or None when it escapes the root or does not
        exist.
    """
    root = examples_dir.resolve()
    candidate = (root / profile / version).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        return None
    if not candidate.is_dir():
        return None
    return candidate


def _no_example_message(profile: str, version: str, *, found: bool) -> str:
    """Why no example could be loaded, with the profile and version escaped.

    Both are stored per dataset and reach this HTML fragment, which htmx swaps
    into the page — so they are content, never markup.
    """
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

        # Newest first, ordered numerically (1.10 after 1.9).
        versions = sorted(versions, key=version_sort_key, reverse=True)
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

    # One entry per specification, not per version: the version selector beside
    # the picker is what chooses between them, and three versions of a profile
    # otherwise read as three identical entries.
    by_name: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        if not draft.name:
            continue
        spec_data = draft.spec_data or {}
        entry = by_name.get(draft.name)
        if entry is None:
            entry = {
                "name": f"draft:{draft.name}",
                "display_name": f"{draft.name} (Draft)",
                "description": spec_data.get("description", ""),
                "root_entity": spec_data.get("root_entity", "Investigation"),
                "versions": [],
                "latest_version": draft.version,
                "source": "draft",
            }
            by_name[draft.name] = entry
            profiles_data.append(entry)
        if draft.version not in entry["versions"]:
            entry["versions"].append(draft.version)
    for entry in by_name.values():
        entry["versions"].sort(key=version_sort_key)
        entry["latest_version"] = entry["versions"][-1]

    # Every published spec, from any account, offered as a starting point:
    # publishing is what makes a specification available to other people.
    specs_result = await session.execute(
        select(Spec)
        .options(selectinload(Spec.created_by))
        .where(
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
            await visible_specs(session, db_user.id),
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
            "error_message": NEW_DATASET_ERRORS.get(request.query_params.get("error", "")),
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
    is_workbook = filename.endswith((".xlsx", ".xls"))

    try:
        if filename.endswith((".yaml", ".yml")):
            data = yaml.safe_load(content.decode("utf-8"))
        elif filename.endswith(".json"):
            data = json.loads(content.decode("utf-8"))
        elif filename.endswith((".xlsx", ".xls")):
            # Parsed once the profile is settled, below: reading a workbook
            # needs the profile to know what each sheet is, and the profile is
            # not known until the form's value has been defaulted.
            data = {}
        else:
            return RedirectResponse("/hub/datasets/new?error=unsupported_format", status_code=302)

        if not data and not is_workbook:
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

        if is_workbook:
            # A facade of the settled profile, only so the parser knows which
            # sheets are entity types. The dataset's own facade does not exist
            # yet, and parsing before the row is created keeps an unreadable
            # upload from leaving an empty dataset behind.
            probe = AppState(profile=profile, version=version)
            data = {
                "_entities_by_type": parse_workbook_sheets(
                    content,
                    profile=profile,
                    version=version,
                    facade=probe.get_or_create_facade(),
                )
            }

    except Exception as e:
        logger.exception(f"Failed to parse import file: {e}")
        return RedirectResponse("/hub/datasets/new?error=parse_error", status_code=302)

    # Create dataset
    try:
        profile, version, spec_draft_id, spec_id = await resolve_profile_choice(
            session, profile, version, tenant.id, db_user.id
        )
        dataset = await create_dataset(
            session,
            tenant_id=tenant.id,
            name=name,
            profile=profile,
            version=version,
            creator_id=db_user.id,
            spec_draft_id=spec_draft_id,
            spec_id=spec_id,
        )
    except ProfileChoiceNotFoundError as missing:
        return RedirectResponse(f"/hub/datasets/new?error={missing.code}", status_code=302)
    except DuplicateDatasetNameError as refused:
        return _duplicate_name_redirect(refused)
    await session.flush()
    await session.refresh(dataset)

    # The row is not committed until its entities are saved: a file whose
    # entities cannot be loaded is refused and nothing is created. This block
    # used to end in ``logger.warning`` and a redirect to an empty dataset.
    try:
        # The dataset's own specification: for a draft- or spec-bound dataset
        # the built-in loader knows nothing of the profile name.
        spec = await dataset_profile_spec(session, dataset)
        root_entity = (spec.root_entity if spec is not None else None) or "Investigation"

        # The dataset was just created empty, so this yields a fresh state whose
        # facade is the authoritative store for the imported entities.
        state = await ensure_dataset_facade_for_write(dataset, session)
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
                nested_errors = create_nested_nodes(
                    state, facade, node, root_entity, copy.deepcopy(entity_data)
                )
                if nested_errors:
                    logger.warning(f"Import errors: {nested_errors[:5]}")

        if state.editing_node_id is None and state.entity_tree:
            state.editing_node_id = state.entity_tree[0].id

        # Save to database with version history
        dataset_id = dataset.id
        await save_dataset_state(session, dataset, state, user)

    except Exception:
        logger.exception("Could not load the imported entities for %s/%s", profile, version)
        await session.rollback()
        return RedirectResponse("/hub/datasets/new?error=import_entities_failed", status_code=302)

    return RedirectResponse(f"/hub/datasets/{dataset_id}", status_code=303)


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
        # Off the event loop: see create_dataset_from_accession.
        client = await run_in_threadpool(run_source_import, dataset.profile, value.strip())
    except LookupError:
        # dataset.profile is a draft's name for spec-backed datasets, so it is
        # text its author chose. Escaped like _import_failure_message does.
        return HTMLResponse(
            f"<div class='notification error'>No importer is registered for the "
            f"{escape(dataset.profile)} profile.</div>",
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
            f"'{escape(value.strip())}'. The archive returned no records of the kind "
            "this importer reads: either the identifier is wrong, or the record "
            "exists but holds no such data (ENA imports sequencing runs, so an "
            "assembly project with no runs imports as nothing).</div>",
            status_code=404,
        )

    state.facade = client.facade
    state.invalidate_cache()
    await save_dataset_state(session, dataset, state, user)

    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/datasets/{dataset_id}"
    return response


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

    try:
        profile, version, spec_draft_id, spec_id = await resolve_profile_choice(
            session, profile, version, tenant.id, db_user.id
        )
    except ProfileChoiceNotFoundError as missing:
        return RedirectResponse(f"/hub/?error={missing.code}", status_code=302)
    try:
        dataset = await create_dataset(
            session,
            tenant_id=tenant.id,
            name=name,
            profile=profile,
            version=version,
            creator_id=db_user.id,
            spec_draft_id=spec_draft_id,
            spec_id=spec_id,
        )
    except DuplicateDatasetNameError as refused:
        return _duplicate_name_redirect(refused)
    await session.flush()
    await session.refresh(dataset)
    dataset_id = dataset.id

    # Committed only once the example, when asked for, has loaded: example
    # data that does not fit is refused and nothing is created, where this
    # used to log the failure and redirect to an empty dataset.
    logger.info(
        f"dataset_create: load_example={load_example!r}, profile={profile}, version={version}"
    )
    if load_example == "true":
        examples_dir = Path(metaseed.__file__).parent / "examples"
        version_dir = example_version_dir(examples_dir, profile, version)
        yaml_files = list(version_dir.glob("*.yaml")) if version_dir else []

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
                state = await ensure_dataset_facade_for_write(dataset, session)
                facade = state.get_or_create_facade()

                node = add_entity_node(state, root_entity, example_data)
                state.editing_node_id = node.id

                # Create nested child nodes from the unmodified copy
                example_errors = create_nested_nodes(
                    state, facade, node, root_entity, example_data_copy
                )
                if example_errors:
                    logger.error(f"Example load errors: {example_errors[:5]}")

                # Save to database with version history
                await save_dataset_state(session, dataset, state, user)
            except Exception:
                logger.exception("Failed to load example data for %s/%s", profile, version)
                await session.rollback()
                return RedirectResponse("/hub/datasets/new?error=example_failed", status_code=302)

    await session.commit()
    return RedirectResponse(f"/hub/datasets/{dataset_id}", status_code=303)


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
        # json.dumps, not f-string interpolation: the exception text is not
        # ours to shape, and hand-escaping only quotes broke the JSON on a
        # backslash or a newline. The detail stays in the log, not the header.
        response = Response(status_code=200)
        response.headers["HX-Trigger"] = json.dumps(
            {"showToast": {"message": "Delete failed. The error has been logged.", "type": "error"}}
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
    version_dir = example_version_dir(examples_dir, dataset.profile, dataset.version)

    if version_dir is None:
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
    except yaml.YAMLError:
        logger.exception("Bundled example %s is not readable YAML", example_file)
        return HTMLResponse(
            "<div class='error'>The example could not be read. The server log has the cause.</div>"
        )

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
        example_errors = create_nested_nodes(state, facade, node, root_entity, example_data_copy)
        if example_errors:
            logger.error(f"Example load errors: {example_errors[:5]}")

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
