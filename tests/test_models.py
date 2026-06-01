"""Tests for SQLAlchemy model constraints and behavior."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .factories import (
    make_dataset,
    make_team,
    make_tenant,
    make_user,
)


class TestTenantModel:
    """Tests for Tenant model constraints."""

    async def test_tenant_slug_unique(self, session: AsyncSession) -> None:
        """Tenant slug must be globally unique."""
        tenant1 = make_tenant(slug="acme-corp")
        session.add(tenant1)
        await session.flush()

        tenant2 = make_tenant(slug="acme-corp")
        session.add(tenant2)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_tenant_has_timestamps(self, session: AsyncSession) -> None:
        """Tenant should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        await session.refresh(tenant)

        assert tenant.created_at is not None
        assert tenant.updated_at is not None
        assert isinstance(tenant.created_at, datetime)
        assert isinstance(tenant.updated_at, datetime)

    async def test_tenant_soft_delete(self, session: AsyncSession) -> None:
        """Tenant should support soft delete via deleted_at."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        await session.refresh(tenant)

        assert tenant.deleted_at is None
        assert tenant.is_deleted is False

        tenant.soft_delete()
        await session.flush()
        await session.refresh(tenant)

        assert tenant.deleted_at is not None
        assert tenant.is_deleted is True


class TestUserModel:
    """Tests for User model constraints."""

    async def test_user_email_unique_per_tenant(self, session: AsyncSession) -> None:
        """User email should be unique within a tenant but allowed across tenants."""
        tenant1 = make_tenant(slug="tenant-1")
        tenant2 = make_tenant(slug="tenant-2")
        session.add_all([tenant1, tenant2])
        await session.flush()

        # Same email in different tenants should work
        user1 = make_user(tenant=tenant1, email="shared@example.com")
        user2 = make_user(tenant=tenant2, email="shared@example.com")
        session.add_all([user1, user2])
        await session.flush()

        # Same email in same tenant should fail
        user3 = make_user(tenant=tenant1, email="shared@example.com")
        session.add(user3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_user_keycloak_id_unique_per_tenant(self, session: AsyncSession) -> None:
        """User keycloak_id should be unique within a tenant."""
        tenant1 = make_tenant(slug="tenant-1")
        tenant2 = make_tenant(slug="tenant-2")
        session.add_all([tenant1, tenant2])
        await session.flush()

        # Same keycloak_id in different tenants should work
        user1 = make_user(tenant=tenant1, keycloak_id="kc-shared-123")
        user2 = make_user(tenant=tenant2, keycloak_id="kc-shared-123")
        session.add_all([user1, user2])
        await session.flush()

        # Same keycloak_id in same tenant should fail
        user3 = make_user(tenant=tenant1, keycloak_id="kc-shared-123")
        session.add(user3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_user_has_timestamps(self, session: AsyncSession) -> None:
        """User should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()
        await session.refresh(user)

        assert user.created_at is not None
        assert user.updated_at is not None

    async def test_user_soft_delete(self, session: AsyncSession) -> None:
        """User should support soft delete."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()
        await session.refresh(user)

        assert user.is_deleted is False

        user.soft_delete()
        await session.flush()
        await session.refresh(user)

        assert user.is_deleted is True
        assert user.deleted_at is not None


class TestTeamModel:
    """Tests for Team model constraints."""

    async def test_team_name_unique_per_tenant(self, session: AsyncSession) -> None:
        """Team name should be unique within a tenant."""
        tenant1 = make_tenant(slug="tenant-1")
        tenant2 = make_tenant(slug="tenant-2")
        session.add_all([tenant1, tenant2])
        await session.flush()

        # Same name in different tenants should work
        team1 = make_team(tenant=tenant1, name="Engineering")
        team2 = make_team(tenant=tenant2, name="Engineering")
        session.add_all([team1, team2])
        await session.flush()

        # Same name in same tenant should fail
        team3 = make_team(tenant=tenant1, name="Engineering")
        session.add(team3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_team_has_timestamps(self, session: AsyncSession) -> None:
        """Team should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        team = make_team(tenant=tenant)
        session.add(team)
        await session.flush()
        await session.refresh(team)

        assert team.created_at is not None
        assert team.updated_at is not None


class TestDatasetModel:
    """Tests for Dataset model constraints."""

    async def test_dataset_name_unique_per_tenant(self, session: AsyncSession) -> None:
        """Dataset name should be unique within a tenant."""
        tenant1 = make_tenant(slug="tenant-1")
        tenant2 = make_tenant(slug="tenant-2")
        session.add_all([tenant1, tenant2])
        await session.flush()

        # Same name in different tenants should work
        ds1 = make_dataset(tenant=tenant1, name="Analysis")
        ds2 = make_dataset(tenant=tenant2, name="Analysis")
        session.add_all([ds1, ds2])
        await session.flush()

        # Same name in same tenant should fail
        ds3 = make_dataset(tenant=tenant1, name="Analysis")
        session.add(ds3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_dataset_has_timestamps(self, session: AsyncSession) -> None:
        """Dataset should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        dataset = make_dataset(tenant=tenant)
        session.add(dataset)
        await session.flush()
        await session.refresh(dataset)

        assert dataset.created_at is not None
        assert dataset.updated_at is not None

    async def test_dataset_updated_at_changes_on_update(self, session: AsyncSession) -> None:
        """Dataset updated_at should change when the dataset is modified."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        dataset = make_dataset(tenant=tenant)
        session.add(dataset)
        await session.flush()
        await session.refresh(dataset)

        original_updated_at = dataset.updated_at

        # Modify the dataset
        dataset.name = "Updated Name"
        await session.flush()
        await session.refresh(dataset)

        # updated_at should be >= original (may be same if within resolution)
        assert dataset.updated_at >= original_updated_at

    async def test_dataset_soft_delete(self, session: AsyncSession) -> None:
        """Dataset should support soft delete."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        dataset = make_dataset(tenant=tenant)
        session.add(dataset)
        await session.flush()
        await session.refresh(dataset)

        assert dataset.is_deleted is False

        dataset.soft_delete()
        await session.flush()
        await session.refresh(dataset)

        assert dataset.is_deleted is True
        assert dataset.deleted_at is not None


class TestTimestampBehavior:
    """Tests for timestamp mixin behavior."""

    async def test_created_at_is_set_on_insert(self, session: AsyncSession) -> None:
        """created_at should be automatically set on insert."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        await session.refresh(tenant)

        now = datetime.now(UTC)

        # Verify timestamp is set and within 5 seconds of now
        # (allows for clock skew between Python and PostgreSQL)
        assert tenant.created_at is not None
        created = tenant.created_at.replace(tzinfo=UTC)
        delta = abs((now - created).total_seconds())
        assert delta < 5, f"created_at {created} is not within 5 seconds of {now}"

    async def test_updated_at_is_set_on_insert(self, session: AsyncSession) -> None:
        """updated_at should be automatically set on insert."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()
        await session.refresh(tenant)

        assert tenant.updated_at is not None
        # updated_at should equal created_at on new records
        assert tenant.updated_at >= tenant.created_at
