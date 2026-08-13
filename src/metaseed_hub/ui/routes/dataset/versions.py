"""Dataset version history and diff routes."""

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from metaseed_hub.models import (
    DatasetVersion,
    User,
)
from metaseed_hub.ui.dependencies import (
    CurrentUser,
    DbSession,
    get_dataset_for_editor,
    get_dataset_for_user,
)
from metaseed_hub.ui.render import render_template
from metaseed_hub.ui.security import csrf_error_response, validate_csrf_or_error

from ._router import router

logger = logging.getLogger("metaseed_hub")


def _flatten_tree(tree: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Flatten tree into dict keyed by node ID with full data."""
    result: dict[str, dict[str, Any]] = {}
    for node in tree:
        node_id = node.get("id", "")
        if node_id:
            result[node_id] = {
                "entity_type": node.get("entity_type", "Unknown"),
                "label": node.get("label", ""),
                "data": node.get("data", {}),
            }
        if "children" in node:
            result.update(_flatten_tree(node["children"]))
    return result


def _calculate_diff(old_data: dict[str, Any], new_data: dict[str, Any]) -> dict[str, Any]:
    """Calculate diff between two dataset states.

    Returns dict with summary changes and detailed field changes.
    """
    old_tree = old_data.get("tree", [])
    new_tree = new_data.get("tree", [])

    def count_entities(tree: list[dict[str, Any]]) -> dict[str, int]:
        """Count entities by type in tree."""
        counts: dict[str, int] = {}
        for node in tree:
            entity_type = node.get("entity_type", "Unknown")
            counts[entity_type] = counts.get(entity_type, 0) + 1
            if "children" in node:
                child_counts = count_entities(node["children"])
                for k, v in child_counts.items():
                    counts[k] = counts.get(k, 0) + v
        return counts

    old_counts = count_entities(old_tree)
    new_counts = count_entities(new_tree)

    all_types = set(old_counts.keys()) | set(new_counts.keys())
    changes: list[str] = []

    for entity_type in sorted(all_types):
        old_count = old_counts.get(entity_type, 0)
        new_count = new_counts.get(entity_type, 0)
        if new_count > old_count:
            changes.append(f"+{new_count - old_count} {entity_type}")
        elif new_count < old_count:
            changes.append(f"-{old_count - new_count} {entity_type}")

    total_old = sum(old_counts.values())
    total_new = sum(new_counts.values())

    return {
        "changes": changes,
        "total_old": total_old,
        "total_new": total_new,
        "has_changes": changes or (old_data != new_data),
    }


def _calculate_detailed_diff(
    old_data: dict[str, Any], new_data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Calculate detailed field-level diff between two dataset states.

    Returns list of changes with entity info and field diffs.
    """
    old_nodes = _flatten_tree(old_data.get("tree", []))
    new_nodes = _flatten_tree(new_data.get("tree", []))

    all_ids = set(old_nodes.keys()) | set(new_nodes.keys())
    changes: list[dict[str, Any]] = []

    for node_id in all_ids:
        old_node = old_nodes.get(node_id)
        new_node = new_nodes.get(node_id)

        if old_node and not new_node:
            # Entity removed
            changes.append(
                {
                    "type": "removed",
                    "entity_type": old_node["entity_type"],
                    "label": old_node["label"],
                    "fields": [],
                }
            )
        elif new_node and not old_node:
            # Entity added
            field_changes = []
            for key, val in new_node["data"].items():
                if val is not None and str(val).strip():
                    field_changes.append(
                        {
                            "field": key,
                            "old": None,
                            "new": val,
                        }
                    )
            changes.append(
                {
                    "type": "added",
                    "entity_type": new_node["entity_type"],
                    "label": new_node["label"],
                    "fields": field_changes,
                }
            )
        elif old_node and new_node:
            # Check for field changes
            old_fields = old_node["data"]
            new_fields = new_node["data"]
            all_keys = set(old_fields.keys()) | set(new_fields.keys())

            field_changes = []
            for key in all_keys:
                old_val = old_fields.get(key)
                new_val = new_fields.get(key)
                if old_val != new_val:
                    field_changes.append(
                        {
                            "field": key,
                            "old": old_val,
                            "new": new_val,
                        }
                    )

            if field_changes:
                changes.append(
                    {
                        "type": "modified",
                        "entity_type": new_node["entity_type"],
                        "label": new_node["label"],
                        "fields": field_changes,
                    }
                )

    return changes


@router.get("/{dataset_id}/versions", response_class=HTMLResponse)
async def get_dataset_versions(
    request: Request,
    dataset_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Get version history for a dataset with diffs."""
    dataset = await get_dataset_for_user(dataset_id, session, user)

    result = await session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .options(selectinload(DatasetVersion.created_by))
        .order_by(DatasetVersion.version_number.desc())
    )
    versions = list(result.scalars().all())

    # Calculate diffs between consecutive versions
    versions_with_diffs: list[dict[str, Any]] = []
    for i, version in enumerate(versions):
        version_data: dict[str, Any] = {
            "version": version,
            "diff": None,
            # A version records the state *after* a save, so the most recent one
            # holds what the dataset already contains. Restoring it can only be a
            # no-op, so the list marks it instead of offering the control.
            "is_current": version.data == dataset.data,
        }
        # Compare with previous version (next in list since sorted desc)
        if i < len(versions) - 1:
            prev_version = versions[i + 1]
            version_data["diff"] = _calculate_diff(prev_version.data, version.data)
        elif i == len(versions) - 1:
            # First version - compare with empty
            version_data["diff"] = _calculate_diff({}, version.data)

        versions_with_diffs.append(version_data)

    return render_template(
        request=request,
        name="partials/dataset_versions.html",
        context={
            "versions": versions_with_diffs,
            "dataset_id": dataset_id,
        },
    )


@router.get("/{dataset_id}/versions/{version_id}/diff", response_class=HTMLResponse)
async def get_version_diff(
    request: Request,
    dataset_id: str,
    version_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Get detailed diff for a specific version."""
    await get_dataset_for_user(dataset_id, session, user)

    # Get the version
    result = await session.execute(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id
        )
    )
    version = result.scalar_one_or_none()

    if not version:
        return HTMLResponse("<div class='error'>Version not found</div>", status_code=404)

    # Get previous version
    prev_result = await session.execute(
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == dataset_id,
            DatasetVersion.version_number < version.version_number,
        )
        .order_by(DatasetVersion.version_number.desc())
        .limit(1)
    )
    prev_version = prev_result.scalar_one_or_none()

    # Calculate detailed diff
    old_data = prev_version.data if prev_version else {}
    detailed_changes = _calculate_detailed_diff(old_data, version.data)

    return render_template(
        request=request,
        name="partials/version_diff.html",
        context={
            "version": version,
            "changes": detailed_changes,
            "dataset_id": dataset_id,
        },
    )


@router.post("/{dataset_id}/versions/{version_id}/restore", response_class=HTMLResponse)
async def restore_dataset_version(
    request: Request,
    dataset_id: str,
    version_id: str,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    """Restore a dataset to a previous version."""
    try:
        validate_csrf_or_error(request)
    except Exception:
        return csrf_error_response()

    dataset = await get_dataset_for_editor(dataset_id, session, user)

    # Get the version to restore
    result = await session.execute(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id, DatasetVersion.dataset_id == dataset_id
        )
    )
    version = result.scalar_one_or_none()

    if not version:
        return HTMLResponse(
            "<div class='notification error'>Version not found</div>",
            status_code=404,
        )

    # Restoring the state the dataset already holds would add a version whose
    # diff is empty and change nothing, which reads as a broken button. Say so
    # instead.
    if version.data == dataset.data:
        return HTMLResponse(
            "<div class='notification'>This version matches the current state — "
            "nothing to restore.</div>",
            status_code=200,
        )

    # Get user from database
    user_result = await session.execute(select(User).where(User.keycloak_id == user.sub))
    db_user = user_result.scalar_one_or_none()
    user_id = db_user.id if db_user else None

    # Restore the data (this will create a new version)
    from sqlalchemy.orm.attributes import flag_modified

    # Get next version number
    max_result = await session.execute(
        select(func.coalesce(func.max(DatasetVersion.version_number), 0)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    max_version = max_result.scalar() or 0

    # Create new version with restored data
    new_version = DatasetVersion(
        dataset_id=dataset_id,
        version_number=max_version + 1,
        data=version.data,
        created_by_id=user_id,
    )
    session.add(new_version)

    # Update dataset
    dataset.data = version.data
    flag_modified(dataset, "data")
    session.add(dataset)
    await session.commit()

    # Return redirect to reload page
    response = HTMLResponse(status_code=200)
    response.headers["HX-Redirect"] = f"/hub/datasets/{dataset_id}"
    return response
