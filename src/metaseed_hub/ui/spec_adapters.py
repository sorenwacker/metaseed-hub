"""Database implementations of spec interfaces.

This module provides database-backed implementations of SpecPersistence
and SpecProvider that allow metaseed-hub to use metaseed's reusable
UI components with PostgreSQL storage.

Note: The abstract interfaces are defined here locally until metaseed
exports them properly (see https://github.com/sorenwacker/metaseed/issues/2).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import UTC
from typing import TYPE_CHECKING, Any

from metaseed.specs.loader import SpecLoader
from metaseed.specs.schema import ProfileSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import Spec, SpecStatus

if TYPE_CHECKING:
    from metaseed_hub.models import User, Workspace


class SpecPersistence(ABC):
    """Abstract interface for saving and managing user specs.

    This interface defines the contract for spec persistence operations,
    separating storage concerns from the UI components.
    """

    @abstractmethod
    async def save(self, spec: ProfileSpec, name: str | None = None) -> str:
        """Save a spec to persistent storage."""
        pass

    @abstractmethod
    async def delete(self, name: str, version: str | None = None) -> bool:
        """Delete a user-created spec."""
        pass

    @abstractmethod
    async def list_user_specs(self) -> list[dict[str, Any]]:
        """List all user-created specs."""
        pass

    @abstractmethod
    async def list_templates(self) -> list[dict[str, Any]]:
        """List available templates (built-in specs)."""
        pass

    @abstractmethod
    async def load_template(self, profile: str, version: str) -> ProfileSpec:
        """Load a template spec for cloning."""
        pass

    @abstractmethod
    def is_builtin_name(self, name: str) -> bool:
        """Check if a name conflicts with a built-in spec."""
        pass


class SpecProvider(ABC):
    """Abstract interface for accessing specs.

    This interface provides read-only access to specifications for
    Explorer and comparison features.
    """

    @abstractmethod
    async def list_profiles(self) -> list[str]:
        """List all available profile names."""
        pass

    @abstractmethod
    async def list_versions(self, profile: str) -> list[str]:
        """List available versions for a profile."""
        pass

    @abstractmethod
    async def get_spec(self, profile: str, version: str) -> ProfileSpec:
        """Load a specific spec."""
        pass

    @abstractmethod
    async def get_display_name(self, profile: str) -> str:
        """Get the display name for a profile."""
        pass


logger = logging.getLogger(__name__)

# Built-in profile names that cannot be shadowed
BUILTIN_PROFILES = {"miappe", "isa", "dissco", "darwin-core"}


class DatabaseSpecPersistence(SpecPersistence):
    """Database-backed implementation of SpecPersistence.

    Stores specs in PostgreSQL via SQLAlchemy, supporting workspaces
    and user ownership.
    """

    def __init__(
        self,
        session: AsyncSession,
        user: User,
        workspace: Workspace | None = None,
    ) -> None:
        """Initialize database persistence.

        Args:
            session: Async SQLAlchemy session.
            user: Current user for ownership.
            workspace: Optional workspace context.
        """
        self._session = session
        self._user = user
        self._workspace = workspace
        self._loader = SpecLoader()

    async def save(self, spec: ProfileSpec, name: str | None = None) -> str:
        """Save spec to database.

        Args:
            spec: The ProfileSpec to save.
            name: Optional name override.

        Returns:
            The spec ID.

        Raises:
            ValueError: If name conflicts with built-in.
        """
        spec_name = name or spec.name
        if not spec_name:
            raise ValueError("Spec name is required")

        if self.is_builtin_name(spec_name):
            raise ValueError(
                f"Cannot save with name '{spec_name}' - conflicts with built-in spec. "
                "Please choose a different name."
            )

        # For now, create a new spec record
        # In a full implementation, this would update existing or create new
        from metaseed_hub.ui.spec_builder.state import SpecBuilderState

        builder = SpecBuilderState(spec=spec)

        db_spec = Spec(
            name=spec_name,
            version=spec.version or "1.0",
            description=spec.description,
            spec_data=builder.to_dict(),
            workspace_id=self._workspace.id if self._workspace else None,
            created_by_id=self._user.id,
            status=SpecStatus.DRAFT,
        )
        self._session.add(db_spec)
        await self._session.flush()

        return str(db_spec.id)

    async def delete(self, name: str, version: str | None = None) -> bool:
        """Soft-delete a spec from database.

        Args:
            name: Spec name.
            version: Optional version.

        Returns:
            True if deleted.

        Raises:
            ValueError: If attempting to delete built-in.
        """
        if self.is_builtin_name(name):
            raise ValueError("Cannot delete built-in specs")

        from datetime import datetime

        query = select(Spec).where(
            Spec.name == name,
            Spec.deleted_at.is_(None),
        )
        if version:
            query = query.where(Spec.version == version)
        if self._workspace:
            query = query.where(Spec.workspace_id == self._workspace.id)

        result = await self._session.execute(query)
        specs = result.scalars().all()

        if not specs:
            return False

        for spec in specs:
            spec.deleted_at = datetime.now(UTC)

        return True

    async def list_user_specs(self) -> list[dict[str, Any]]:
        """List user-created specs from database.

        Returns:
            List of spec metadata dicts.
        """
        query = select(Spec).where(
            Spec.deleted_at.is_(None),
            Spec.status == SpecStatus.PUBLISHED,
        )
        if self._workspace:
            query = query.where(Spec.workspace_id == self._workspace.id)

        query = query.order_by(Spec.name, Spec.version.desc())
        result = await self._session.execute(query)
        specs = result.scalars().all()

        # Group by name
        by_name: dict[str, list[Spec]] = {}
        for spec in specs:
            if spec.name not in by_name:
                by_name[spec.name] = []
            by_name[spec.name].append(spec)

        return [
            {
                "name": name,
                "display_name": versions[0].name,
                "versions": [s.version for s in versions],
                "description": versions[0].description or "",
            }
            for name, versions in by_name.items()
        ]

    async def list_templates(self) -> list[dict[str, Any]]:
        """List built-in templates from SpecLoader.

        Returns:
            List of template metadata dicts.
        """
        templates = []
        for profile_name in self._loader.list_profiles():
            versions = self._loader.list_versions(profile_name)
            if versions:
                try:
                    spec = self._loader.load_profile(versions[0], profile_name)
                    templates.append(
                        {
                            "name": profile_name,
                            "display_name": spec.display_name or profile_name,
                            "versions": versions,
                            "description": spec.description or "",
                        }
                    )
                except Exception as e:
                    logger.warning("Failed to load template %s: %s", profile_name, e)

        return templates

    async def load_template(self, profile: str, version: str) -> ProfileSpec:
        """Load a built-in template.

        Args:
            profile: Profile name.
            version: Version string.

        Returns:
            The ProfileSpec.
        """
        return self._loader.load_profile(version, profile)

    def is_builtin_name(self, name: str) -> bool:
        """Check if name conflicts with built-in.

        Args:
            name: Name to check.

        Returns:
            True if conflicts.
        """
        return name.lower() in BUILTIN_PROFILES


class DatabaseSpecProvider(SpecProvider):
    """Database-backed implementation of SpecProvider.

    Provides unified access to both built-in specs and database-stored
    published specs for the Explorer component.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize provider.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session
        self._loader = SpecLoader()

    async def list_profiles(self) -> list[str]:
        """List all available profiles (built-in + database).

        Returns:
            Sorted list of profile names.
        """
        # Built-in profiles
        profiles = set(self._loader.list_profiles())

        # Database profiles
        result = await self._session.execute(
            select(Spec.name)
            .distinct()
            .where(
                Spec.deleted_at.is_(None),
                Spec.status == SpecStatus.PUBLISHED,
            )
        )
        for (name,) in result.all():
            profiles.add(name)

        return sorted(profiles)

    async def list_versions(self, profile: str) -> list[str]:
        """List versions for a profile.

        Args:
            profile: Profile name.

        Returns:
            List of versions, newest first.
        """
        versions = []

        # Check built-in first
        try:
            builtin_versions = self._loader.list_versions(profile)
            versions.extend(builtin_versions)
        except Exception:
            pass

        # Check database
        result = await self._session.execute(
            select(Spec.version)
            .where(
                Spec.name == profile,
                Spec.deleted_at.is_(None),
                Spec.status == SpecStatus.PUBLISHED,
            )
            .order_by(Spec.version.desc())
        )
        for (version,) in result.all():
            if version not in versions:
                versions.append(version)

        if not versions:
            raise FileNotFoundError(f"Profile not found: {profile}")

        return versions

    async def get_spec(self, profile: str, version: str) -> ProfileSpec:
        """Load a specific spec.

        Args:
            profile: Profile name.
            version: Version string.

        Returns:
            The ProfileSpec.
        """
        # Try built-in first
        try:
            return self._loader.load_profile(version, profile)
        except Exception:
            pass

        # Try database
        result = await self._session.execute(
            select(Spec).where(
                Spec.name == profile,
                Spec.version == version,
                Spec.deleted_at.is_(None),
                Spec.status == SpecStatus.PUBLISHED,
            )
        )
        db_spec = result.scalar_one_or_none()

        if not db_spec:
            raise FileNotFoundError(f"Spec not found: {profile}/{version}")

        # Convert from database format
        from metaseed_hub.ui.spec_builder.state import SpecBuilderState

        builder = SpecBuilderState.from_dict(db_spec.spec_data)
        if not builder.spec:
            raise ValueError(f"Invalid spec data for {profile}/{version}")

        return builder.spec

    async def get_display_name(self, profile: str) -> str:
        """Get display name for a profile.

        Args:
            profile: Profile name.

        Returns:
            Display name or profile name.
        """
        # Try built-in first
        try:
            spec = self._loader.load_profile(self._loader.list_versions(profile)[0], profile)
            return spec.display_name or profile
        except Exception:
            pass

        # Try database
        result = await self._session.execute(
            select(Spec.name)
            .where(
                Spec.name == profile,
                Spec.deleted_at.is_(None),
                Spec.status == SpecStatus.PUBLISHED,
            )
            .limit(1)
        )
        row = result.first()
        if row:
            return str(row[0])

        return profile
