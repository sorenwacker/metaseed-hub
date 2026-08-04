"""Loading and persisting a dataset's AppState (facade + entity tree)."""

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset, DatasetVersion
from metaseed_hub.ui.helpers.tree import serialize_tree
from metaseed_hub.ui.metaseed_ui import AppState

if TYPE_CHECKING:
    from collections.abc import Callable

    from metaseed import MetaseedClient, SkippedNode

    from metaseed_hub.auth import TokenUser

logger = logging.getLogger("metaseed_hub")


def _client_from_spec_data(raw_data: dict[str, Any]) -> "MetaseedClient":
    """Build a client from stored spec data, unwrapping SpecBuilderState.

    Both spec drafts and published specs store ``spec_data`` that may be in
    SpecBuilderState format with the ProfileSpec nested under a ``"spec"`` key.

    Args:
        raw_data: The stored ``spec_data`` payload.

    Returns:
        MetaseedClient built from the contained ProfileSpec data.
    """
    from metaseed import MetaseedClient

    if isinstance(raw_data, dict) and "spec" in raw_data:
        raw_data = raw_data["spec"]
    return MetaseedClient.from_spec(raw_data)


def _has_stored_entities(data: dict[str, Any] | None) -> bool:
    """Report whether a stored payload contains entities that a save could lose.

    Args:
        data: The stored ``dataset.data`` payload.

    Returns:
        True if the payload holds a non-empty ``tree`` or flat ``entities`` list.
    """
    if not data:
        return False
    return bool(data.get("tree") or data.get("entities"))


async def ensure_dataset_facade(
    dataset: Dataset,
    session: AsyncSession,
    on_skip: "Callable[[SkippedNode], None] | None" = None,
) -> AppState:
    """Load a dataset into an AppState whose facade holds the stored entities.

    This is the single load path: the facade is the source of truth for entity
    data, so every loaded state must carry one. For datasets using
    database-stored specs (spec_draft_id or spec_id), the spec is loaded and a
    MetaseedClient is created with from_spec(); built-in profiles get a
    standard client.

    The payload is loaded with ``MetaseedClient.load(..., on_skip=...)``, which
    reconstructs entities permissively (``skip_validation``), so incomplete
    drafts and legacy payloads load without loss, and drops -- rather than
    fails on -- a node the profile cannot place. Every drop is logged with the
    dataset id and passed to ``on_skip``: a dropped node is absent from the
    facade, so the next save deletes it from storage, which callers that report
    to a user or an agent must be able to say (see
    ``metaseed_hub.ui.helpers.load_report``).

    Args:
        dataset: Dataset model with profile, version, and optional spec ids.
        session: Database session for loading spec drafts.
        on_skip: Called once per dropped node. Optional: omitting it changes
            nothing about the load, only whether the caller hears about the
            drops, which are logged either way.

    Returns:
        AppState with facade ready to use.

    Raises:
        DatasetDataLoadError: If the dataset holds entity data that could not
            be loaded. This must propagate: returning an empty state instead
            would let the next save overwrite the stored entity tree.
    """
    from metaseed import MetaseedClient

    from metaseed_hub.models import Spec, SpecDraft

    # Always load fresh from database - no caching
    state = AppState()
    state.profile = dataset.profile
    state.version = dataset.version

    client: MetaseedClient | None = None
    # Why the client could not be built. Reported with the refusal below: on its
    # own "no client" says a dataset will not open but not what to fix, and the
    # cause (a profile version that no longer exists, a draft with no spec data)
    # is exactly what the owner needs to hear.
    load_failure: str | None = None

    # Load spec from database FIRST if dataset uses a user-defined spec
    if dataset.spec_draft_id:
        try:
            spec_draft = await session.get(SpecDraft, dataset.spec_draft_id)
            if spec_draft and spec_draft.spec_data:
                client = _client_from_spec_data(spec_draft.spec_data)
                state.facade = client.facade
                # Update state.profile to match facade's lowercased version
                state.profile = state.facade.profile
                logger.debug(f"Loaded draft spec for dataset {dataset.id}: {dataset.profile}")
            else:
                load_failure = f"its draft specification {dataset.spec_draft_id} holds no spec data"
                logger.warning(
                    f"No spec data found for dataset {dataset.id} "
                    f"spec_draft_id={dataset.spec_draft_id}"
                )
        except Exception as e:
            load_failure = f"its draft specification could not be loaded: {e}"
            logger.error(f"Failed to load spec for dataset {dataset.id}: {e}")
            # Don't re-raise - let downstream code handle missing facade
    elif dataset.spec_id:
        # Created from a published specification. Loaded even if the spec has
        # since been withdrawn: the dataset was built against it and must keep
        # opening and validating, which is why the foreign key is SET NULL
        # rather than CASCADE.
        try:
            published = await session.get(Spec, dataset.spec_id)
            if published and published.spec_data:
                client = _client_from_spec_data(published.spec_data)
                state.facade = client.facade
                state.profile = state.facade.profile
            else:
                load_failure = f"its published specification {dataset.spec_id} holds no spec data"
                logger.warning(f"No spec data for dataset {dataset.id} spec_id={dataset.spec_id}")
        except Exception as e:
            load_failure = f"its published specification could not be loaded: {e}"
            logger.error(f"Failed to load published spec for dataset {dataset.id}: {e}")
    else:
        # Built-in profile: create standard client
        try:
            client = MetaseedClient(dataset.profile, dataset.version)
            state.facade = client.facade
        except Exception as e:
            load_failure = f"profile {dataset.profile} {dataset.version} could not be loaded: {e}"
            logger.error(f"Failed to load profile {dataset.profile}: {e}")

    # Load entities into the facade's internal store using client.load().
    # A failure here must raise, not fall through to an empty state: mutation
    # routes save the facade's contents, so an empty state would overwrite the
    # stored entity tree on the next save.
    if _has_stored_entities(dataset.data):
        from metaseed_hub.ui.services.exceptions import DatasetDataLoadError

        if client is None:
            reason = load_failure or "no specification is recorded for it"
            raise DatasetDataLoadError(
                f"No client for dataset {dataset.id}; cannot load its stored "
                f"entities because {reason}",
                user_message=(
                    f"This dataset's schema could not be loaded because {reason}. "
                    "Editing is disabled to protect the stored data."
                ),
            )

        def report(skip: "SkippedNode") -> None:
            """Log a dropped node, then hand it to the caller if one is listening."""
            logger.warning(
                "Dataset %s: skipped a stored %s node (%s), dropping %d node(s) below it",
                dataset.id,
                skip.entity_type or "untyped",
                skip.reason,
                skip.descendants_dropped,
            )
            if on_skip is not None:
                on_skip(skip)

        try:
            count = client.load(dataset.data, on_skip=report)
            logger.debug(f"Loaded {count} entities for dataset {dataset.id}")
        except Exception as e:
            logger.error(f"Failed to load entities for dataset {dataset.id}: {e}")
            raise DatasetDataLoadError(
                f"Failed to load entities for dataset {dataset.id}: {e}",
                user_message="The stored entities for this dataset could not be "
                "loaded. Editing is disabled to protect the stored data.",
            ) from e
        # Invalidate AppState cache to rebuild from facade
        state.invalidate_cache()

    return state


async def save_dataset_state(
    session: AsyncSession,
    dataset: Dataset,
    state: AppState,
    user: "TokenUser | None" = None,
) -> None:
    """Save AppState entity tree to database and create a version.

    Args:
        session: Database session.
        dataset: Dataset model to update.
        state: AppState with entity tree to save.
        user: The acting user; when given, the created version records them as
            author (``created_by_id``). Optional so background/non-request
            callers can still persist without authorship.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm.attributes import flag_modified

    from metaseed_hub.models import User
    from metaseed_hub.ui.helpers.spec_hash import dataset_spec_hash, stamp_spec_hash

    # Stamped here rather than inside serialize_tree: the hash comes from the
    # database row, and serialize_tree only has the in-memory facade.
    new_data = stamp_spec_hash(serialize_tree(state), await dataset_spec_hash(session, dataset))

    # Resolve the acting user's database id for version authorship.
    created_by_id: str | None = None
    if user is not None:
        db_user = (
            await session.execute(select(User).where(User.keycloak_id == user.keycloak_id))
        ).scalar_one_or_none()
        created_by_id = db_user.id if db_user else None

    # Only create version if data changed
    if new_data != dataset.data:
        # Get next version number
        result = await session.execute(
            select(func.coalesce(func.max(DatasetVersion.version_number), 0)).where(
                DatasetVersion.dataset_id == dataset.id
            )
        )
        max_version = result.scalar() or 0

        # Create version with new data
        version = DatasetVersion(
            dataset_id=dataset.id,
            version_number=max_version + 1,
            data=new_data,
            created_by_id=created_by_id,
        )
        session.add(version)

    dataset.data = new_data
    flag_modified(dataset, "data")
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
