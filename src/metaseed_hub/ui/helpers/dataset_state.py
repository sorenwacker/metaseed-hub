"""Loading and persisting a dataset's AppState (facade + entity tree)."""

import logging
from typing import Any

from metaseed.ui.state import AppState

from metaseed_hub.models import Dataset, DatasetVersion
from metaseed_hub.ui.helpers.tree import deserialize_tree, serialize_tree

logger = logging.getLogger("metaseed_hub")


def get_dataset_state(dataset: Dataset) -> AppState:
    """Create AppState for a dataset from database.

    Args:
        dataset: Dataset model with profile, version, and data fields.

    Returns:
        AppState populated with dataset's entity tree.
    """
    state = AppState()
    state.profile = dataset.profile
    state.version = dataset.version
    if dataset.data:
        deserialize_tree(state, dataset.data)
    return state


async def ensure_dataset_facade(
    dataset: Dataset,
    session: Any,
) -> AppState:
    """Get dataset state and ensure facade is properly set for user-defined specs.

    For datasets using database-stored specs (spec_draft_id), this loads the spec
    and creates a MetaseedClient with from_spec(). For built-in profiles, it creates
    a standard client.

    Uses MetaseedClient.load() to populate the facade's internal entity store,
    which is required for operations like to_graph() and get_roots().

    Args:
        dataset: Dataset model with profile, version, and optional spec_draft_id.
        session: Database session for loading spec drafts.

    Returns:
        AppState with facade ready to use.
    """
    from metaseed import MetaseedClient

    from metaseed_hub.models import SpecDraft

    # Always load fresh from database - no caching
    state = AppState()
    state.profile = dataset.profile
    state.version = dataset.version

    client: MetaseedClient | None = None

    # Load spec from database FIRST if dataset uses a user-defined spec
    if dataset.spec_draft_id:
        try:
            spec_draft = await session.get(SpecDraft, dataset.spec_draft_id)
            if spec_draft and spec_draft.spec_data:
                # spec_data may be SpecBuilderState format with spec nested under "spec" key
                raw_data = spec_draft.spec_data
                if isinstance(raw_data, dict) and "spec" in raw_data:
                    raw_data = raw_data["spec"]
                # Create client from custom spec
                client = MetaseedClient.from_spec(raw_data)
                state.facade = client.facade
                # Update state.profile to match facade's lowercased version
                state.profile = state.facade.profile
                logger.debug(f"Loaded draft spec for dataset {dataset.id}: {dataset.profile}")
            else:
                logger.warning(
                    f"No spec data found for dataset {dataset.id} "
                    f"spec_draft_id={dataset.spec_draft_id}"
                )
        except Exception as e:
            logger.error(f"Failed to load spec for dataset {dataset.id}: {e}")
            # Don't re-raise - let downstream code handle missing facade
    else:
        # Built-in profile: create standard client
        try:
            client = MetaseedClient(dataset.profile, dataset.version)
            state.facade = client.facade
        except Exception as e:
            logger.error(f"Failed to load profile {dataset.profile}: {e}")

    # Load entities into facade's internal store using client.load()
    # This is required for to_graph() and other facade operations
    if client and dataset.data:
        try:
            count = client.load(dataset.data)
            logger.debug(f"Loaded {count} entities for dataset {dataset.id}")
            # Invalidate AppState cache to rebuild from facade
            state.invalidate_cache()
        except Exception as e:
            logger.error(f"Failed to load entities for dataset {dataset.id}: {e}")

    return state


async def save_dataset_state(
    session: Any,
    dataset: Dataset,
    state: AppState,
    user_id: str | None = None,
) -> None:
    """Save AppState entity tree to database and create a version.

    Args:
        session: Database session.
        dataset: Dataset model to update.
        state: AppState with entity tree to save.
        user_id: Optional user ID for version tracking.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm.attributes import flag_modified

    new_data = serialize_tree(state)

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
            created_by_id=user_id,
        )
        session.add(version)

    dataset.data = new_data
    flag_modified(dataset, "data")
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
