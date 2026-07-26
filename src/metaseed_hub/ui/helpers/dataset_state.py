"""Loading and persisting a dataset's AppState (facade + entity tree)."""

import logging
from typing import TYPE_CHECKING

from metaseed.ui.state import AppState
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset, DatasetVersion
from metaseed_hub.ui.helpers.tree import deserialize_tree, serialize_tree

if TYPE_CHECKING:
    from metaseed_hub.auth import TokenUser

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
    session: AsyncSession,
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

    new_data = serialize_tree(state)

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
