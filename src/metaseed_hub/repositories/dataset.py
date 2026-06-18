"""Database-backed dataset repository for metaseed-hub."""

from typing import Any

from metaseed.repositories import AsyncDatasetRepository, DatasetData, DatasetInfo
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Dataset


def _tree_to_entities(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert hub tree format to flat entity list.

    Args:
        tree: List of tree nodes with nested children.

    Returns:
        Flat list of entities with _type and _parent_unique_id.
    """
    entities: list[dict[str, Any]] = []

    def flatten(node: dict[str, Any], parent_unique_id: str | None = None) -> None:
        entity_type = node.get("entity_type")
        data = node.get("data", {})

        entity = {"_type": entity_type, "_parent_unique_id": parent_unique_id, **data}
        entities.append(entity)

        unique_id = data.get("unique_id")
        for child in node.get("children", []):
            flatten(child, unique_id)

    for root in tree:
        flatten(root)

    return entities


def _entities_to_tree(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert flat entity list to hub tree format.

    Args:
        entities: Flat list of entities with _type and _parent_unique_id.

    Returns:
        List of tree nodes with nested children.
    """
    from uuid import uuid4

    nodes_by_unique_id: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []

    for entity in entities:
        entity_type = entity.get("_type")
        parent_unique_id = entity.get("_parent_unique_id")

        if not entity_type:
            continue

        # Build the node data without the structural keys, leaving the caller's
        # entity dict untouched (do not pop from the input).
        data = {k: v for k, v in entity.items() if k not in ("_type", "_parent_unique_id")}

        unique_id = data.get("unique_id", str(uuid4()))
        label = data.get("title") or data.get("name") or f"New {entity_type}"

        node = {
            "id": str(uuid4()),
            "entity_type": entity_type,
            "label": label,
            "parent_id": None,
            "data": data,
            "children": [],
        }

        nodes_by_unique_id[unique_id] = node

        if parent_unique_id and parent_unique_id in nodes_by_unique_id:
            parent = nodes_by_unique_id[parent_unique_id]
            node["parent_id"] = parent["id"]
            parent["children"].append(node)
        else:
            roots.append(node)

    return roots


class DatabaseDatasetRepository(AsyncDatasetRepository):  # type: ignore[misc]
    """Database-backed dataset storage for metaseed-hub.

    This repository is tenant-scoped. Each instance operates within
    a single tenant context.
    """

    def __init__(self, session: AsyncSession, tenant_id: str) -> None:
        """Initialize repository with database session and tenant.

        Args:
            session: Async SQLAlchemy session.
            tenant_id: Tenant to scope operations to.
        """
        self._session = session
        self._tenant_id = tenant_id

    async def list(self) -> list[DatasetInfo]:
        """List all datasets in the tenant.

        Returns:
            List of dataset info summaries.
        """
        result = await self._session.execute(
            select(Dataset).where(
                Dataset.tenant_id == self._tenant_id,
                Dataset.deleted_at.is_(None),
            )
        )
        datasets = result.scalars().all()

        return [
            DatasetInfo(
                name=ds.name,
                profile=ds.profile,
                version=ds.version,
                entity_count=self._count_entities(ds.data),
                modified=ds.updated_at.isoformat() if ds.updated_at else "",
            )
            for ds in datasets
        ]

    async def save(self, name: str, data: DatasetData) -> DatasetInfo:
        """Save a dataset.

        Args:
            name: Dataset name.
            data: Dataset contents.

        Returns:
            Updated dataset info.

        Raises:
            ValueError: If name is invalid.
        """
        error = self.validate_name(name)
        if error:
            raise ValueError(error)

        # Look up by (tenant, name) without filtering deleted_at: the unique
        # constraint uq_datasets_tenant_name is not scoped to deleted_at, so at
        # most one row exists per (tenant, name). If it was soft-deleted, reuse
        # and restore it instead of inserting a colliding row.
        result = await self._session.execute(
            select(Dataset).where(
                Dataset.tenant_id == self._tenant_id,
                Dataset.name == name,
            )
        )
        dataset = result.scalar_one_or_none()

        tree = _entities_to_tree(data.entities)
        db_data = {
            "profile": data.profile,
            "version": data.version,
            "tree": tree,
        }

        if dataset:
            if dataset.is_deleted:
                dataset.restore()
            dataset.profile = data.profile
            dataset.version = data.version
            dataset.data = db_data
        else:
            dataset = Dataset(
                tenant_id=self._tenant_id,
                name=name,
                profile=data.profile,
                version=data.version,
                data=db_data,
            )
            self._session.add(dataset)

        await self._session.commit()
        await self._session.refresh(dataset)

        return DatasetInfo(
            name=dataset.name,
            profile=dataset.profile,
            version=dataset.version,
            entity_count=self._count_entities(dataset.data),
            modified=dataset.updated_at.isoformat() if dataset.updated_at else "",
        )

    async def load(self, name: str) -> DatasetData:
        """Load a dataset by name.

        Args:
            name: Dataset name.

        Returns:
            Full dataset contents.

        Raises:
            FileNotFoundError: If dataset not found.
        """
        result = await self._session.execute(
            select(Dataset).where(
                Dataset.tenant_id == self._tenant_id,
                Dataset.name == name,
                Dataset.deleted_at.is_(None),
            )
        )
        dataset = result.scalar_one_or_none()

        if not dataset:
            raise FileNotFoundError(f"Dataset not found: {name}")

        data = dataset.data or {}
        tree = data.get("tree", [])
        entities = _tree_to_entities(tree)

        return DatasetData(
            name=dataset.name,
            profile=dataset.profile,
            version=dataset.version,
            entities=entities,
            modified=dataset.updated_at.isoformat() if dataset.updated_at else "",
        )

    async def delete(self, name: str) -> bool:
        """Delete a dataset (soft delete).

        Args:
            name: Dataset name.

        Returns:
            True if deleted, False if not found.
        """
        result = await self._session.execute(
            select(Dataset).where(
                Dataset.tenant_id == self._tenant_id,
                Dataset.name == name,
                Dataset.deleted_at.is_(None),
            )
        )
        dataset = result.scalar_one_or_none()

        if not dataset:
            return False

        dataset.soft_delete()
        await self._session.commit()
        return True

    async def exists(self, name: str) -> bool:
        """Check if a dataset exists.

        Args:
            name: Dataset name.

        Returns:
            True if exists, False otherwise.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(Dataset)
            .where(
                Dataset.tenant_id == self._tenant_id,
                Dataset.name == name,
                Dataset.deleted_at.is_(None),
            )
        )
        return result.scalar_one() > 0

    @staticmethod
    def _count_entities(data: dict[str, Any] | None) -> int:
        """Count entities in dataset data.

        Args:
            data: Dataset data with tree structure.

        Returns:
            Total entity count.
        """
        if not data:
            return 0

        tree = data.get("tree", [])
        return len(_tree_to_entities(tree))
