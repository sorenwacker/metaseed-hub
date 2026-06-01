"""Tests for multi-spec and team collaboration functionality."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import (
    Spec,
    SpecDraft,
    SpecStatus,
    TeamMembership,
    TeamRole,
)

from .factories import (
    make_spec,
    make_spec_draft,
    make_team,
    make_team_membership,
    make_tenant,
    make_user,
)


class TestSpecModel:
    """Tests for Spec model constraints."""

    async def test_spec_name_version_unique_per_tenant(self, session: AsyncSession) -> None:
        """Spec name+version should be unique within a tenant."""
        tenant1 = make_tenant(slug="tenant-1")
        tenant2 = make_tenant(slug="tenant-2")
        session.add_all([tenant1, tenant2])
        await session.flush()

        user1 = make_user(tenant=tenant1)
        user2 = make_user(tenant=tenant2)
        session.add_all([user1, user2])
        await session.flush()

        # Same name+version in different tenants should work
        spec1 = make_spec(tenant=tenant1, created_by=user1, name="MySpec", version="1.0")
        spec2 = make_spec(tenant=tenant2, created_by=user2, name="MySpec", version="1.0")
        session.add_all([spec1, spec2])
        await session.flush()

        # Same name+version in same tenant should fail
        spec3 = make_spec(tenant=tenant1, created_by=user1, name="MySpec", version="1.0")
        session.add(spec3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_spec_different_versions_allowed(self, session: AsyncSession) -> None:
        """Same spec name with different versions should be allowed."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec1 = make_spec(tenant=tenant, created_by=user, name="MySpec", version="1.0")
        spec2 = make_spec(tenant=tenant, created_by=user, name="MySpec", version="2.0")
        session.add_all([spec1, spec2])
        await session.flush()

        assert spec1.id != spec2.id

    async def test_spec_has_timestamps(self, session: AsyncSession) -> None:
        """Spec should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec = make_spec(tenant=tenant, created_by=user)
        session.add(spec)
        await session.flush()
        await session.refresh(spec)

        assert spec.created_at is not None
        assert spec.updated_at is not None

    async def test_spec_soft_delete(self, session: AsyncSession) -> None:
        """Spec should support soft delete."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec = make_spec(tenant=tenant, created_by=user)
        session.add(spec)
        await session.flush()
        await session.refresh(spec)

        assert spec.is_deleted is False

        spec.soft_delete()
        await session.flush()
        await session.refresh(spec)

        assert spec.is_deleted is True
        assert spec.deleted_at is not None

    async def test_spec_status_values(self, session: AsyncSession) -> None:
        """Spec status should accept valid enum values."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        # Test all status values
        for status in SpecStatus:
            spec = make_spec(
                tenant=tenant,
                created_by=user,
                name=f"Spec{status.value}",
                status=status,
            )
            session.add(spec)
            await session.flush()
            await session.refresh(spec)
            assert spec.status == status


class TestSpecDraftModel:
    """Tests for SpecDraft model constraints."""

    async def test_draft_name_unique_per_tenant_user(self, session: AsyncSession) -> None:
        """Draft name should be unique per tenant+user combination."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user1 = make_user(tenant=tenant, email="user1@test.com")
        user2 = make_user(tenant=tenant, email="user2@test.com")
        session.add_all([user1, user2])
        await session.flush()

        # Same name by different users in same tenant should work
        draft1 = make_spec_draft(tenant=tenant, user=user1, name="MyDraft")
        draft2 = make_spec_draft(tenant=tenant, user=user2, name="MyDraft")
        session.add_all([draft1, draft2])
        await session.flush()

        # Same name by same user in same tenant should fail
        draft3 = make_spec_draft(tenant=tenant, user=user1, name="MyDraft")
        session.add(draft3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_user_can_have_multiple_drafts(self, session: AsyncSession) -> None:
        """User can have multiple drafts with different names in same tenant."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        draft1 = make_spec_draft(tenant=tenant, user=user, name="Draft1")
        draft2 = make_spec_draft(tenant=tenant, user=user, name="Draft2")
        draft3 = make_spec_draft(tenant=tenant, user=user, name="Draft3")
        session.add_all([draft1, draft2, draft3])
        await session.flush()

        # Verify all drafts exist
        result = await session.execute(select(SpecDraft).where(SpecDraft.user_id == user.id))
        drafts = result.scalars().all()
        assert len(drafts) == 3

    async def test_draft_same_name_different_tenants(self, session: AsyncSession) -> None:
        """Same draft name allowed in different tenants."""
        tenant1 = make_tenant(slug="tenant-1")
        tenant2 = make_tenant(slug="tenant-2")
        session.add_all([tenant1, tenant2])
        await session.flush()

        user1 = make_user(tenant=tenant1)
        user2 = make_user(tenant=tenant2)
        session.add_all([user1, user2])
        await session.flush()

        draft1 = make_spec_draft(tenant=tenant1, user=user1, name="MyDraft")
        draft2 = make_spec_draft(tenant=tenant2, user=user2, name="MyDraft")
        session.add_all([draft1, draft2])
        await session.flush()

        assert draft1.id != draft2.id

    async def test_draft_has_timestamps(self, session: AsyncSession) -> None:
        """SpecDraft should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        draft = make_spec_draft(tenant=tenant, user=user)
        session.add(draft)
        await session.flush()
        await session.refresh(draft)

        assert draft.created_at is not None
        assert draft.updated_at is not None

    async def test_draft_source_spec_relationship(self, session: AsyncSession) -> None:
        """Draft can reference a source spec for editing published specs."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec = make_spec(tenant=tenant, created_by=user, name="OriginalSpec")
        session.add(spec)
        await session.flush()

        draft = make_spec_draft(
            tenant=tenant,
            user=user,
            name="EditingOriginalSpec",
            source_spec=spec,
        )
        session.add(draft)
        await session.flush()
        await session.refresh(draft)

        assert draft.source_spec_id == spec.id


class TestTeamBasedAccess:
    """Tests for team-based access control patterns."""

    async def test_user_team_membership(self, session: AsyncSession) -> None:
        """User can be member of team in tenant."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        team = make_team(tenant=tenant, name="Research Team")
        session.add(team)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        # Add user to team
        membership = make_team_membership(user=user, team=team, role=TeamRole.MEMBER)
        session.add(membership)
        await session.flush()

        # Query teams user belongs to
        result = await session.execute(
            select(TeamMembership).where(TeamMembership.user_id == user.id)
        )
        memberships = result.scalars().all()

        assert len(memberships) == 1
        assert memberships[0].team_id == team.id

    async def test_admin_role_in_team(self, session: AsyncSession) -> None:
        """Admin role in team should be queryable."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        team = make_team(tenant=tenant)
        session.add(team)
        await session.flush()

        admin_user = make_user(tenant=tenant, email="admin@test.com")
        member_user = make_user(tenant=tenant, email="member@test.com")
        session.add_all([admin_user, member_user])
        await session.flush()

        admin_membership = make_team_membership(user=admin_user, team=team, role=TeamRole.ADMIN)
        member_membership = make_team_membership(user=member_user, team=team, role=TeamRole.MEMBER)
        session.add_all([admin_membership, member_membership])
        await session.flush()

        # Query to check admin role
        result = await session.execute(
            select(TeamMembership).where(
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == admin_user.id,
                TeamMembership.role.in_([TeamRole.ADMIN, TeamRole.OWNER]),
            )
        )
        admin_access = result.scalar_one_or_none()
        assert admin_access is not None

        # Member should not have admin access
        result = await session.execute(
            select(TeamMembership).where(
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == member_user.id,
                TeamMembership.role.in_([TeamRole.ADMIN, TeamRole.OWNER]),
            )
        )
        member_admin_access = result.scalar_one_or_none()
        assert member_admin_access is None


class TestPublishingWorkflow:
    """Tests for draft to published spec workflow."""

    async def test_publish_draft_creates_spec(self, session: AsyncSession) -> None:
        """Publishing a draft should create a spec with same data."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec_data = {"entities": {"MyEntity": {"fields": []}}}
        draft = make_spec_draft(
            tenant=tenant,
            user=user,
            name="MySpec",
            version="1.0",
            spec_data=spec_data,
        )
        session.add(draft)
        await session.flush()

        # Simulate publishing: create spec from draft data
        spec = Spec(
            tenant_id=draft.tenant_id,
            name=draft.name,
            version=draft.version,
            spec_data=draft.spec_data,
            status=SpecStatus.PUBLISHED,
            created_by_id=user.id,
        )
        session.add(spec)
        await session.flush()

        assert spec.name == draft.name
        assert spec.version == draft.version
        assert spec.spec_data == draft.spec_data
        assert spec.status == SpecStatus.PUBLISHED

    async def test_edit_published_spec_creates_draft(self, session: AsyncSession) -> None:
        """Editing a published spec should create a draft with source_spec reference."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec_data = {"entities": {"Original": {"fields": []}}}
        spec = make_spec(
            tenant=tenant,
            created_by=user,
            name="PublishedSpec",
            version="1.0",
            spec_data=spec_data,
        )
        session.add(spec)
        await session.flush()

        # Create draft from published spec
        draft = make_spec_draft(
            tenant=tenant,
            user=user,
            name=spec.name,
            version="1.1",  # New version
            spec_data=spec.spec_data,
            source_spec=spec,
        )
        session.add(draft)
        await session.flush()
        await session.refresh(draft)

        assert draft.source_spec_id == spec.id
        assert draft.spec_data == spec.spec_data

    async def test_publish_new_version(self, session: AsyncSession) -> None:
        """Publishing a new version should create separate spec."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        # Publish v1.0
        spec_v1 = make_spec(
            tenant=tenant,
            created_by=user,
            name="MySpec",
            version="1.0",
        )
        session.add(spec_v1)
        await session.flush()

        # Publish v2.0
        spec_v2 = make_spec(
            tenant=tenant,
            created_by=user,
            name="MySpec",
            version="2.0",
        )
        session.add(spec_v2)
        await session.flush()

        # Both versions should exist
        result = await session.execute(
            select(Spec).where(
                Spec.tenant_id == tenant.id,
                Spec.name == "MySpec",
            )
        )
        specs = result.scalars().all()
        assert len(specs) == 2
        versions = {s.version for s in specs}
        assert versions == {"1.0", "2.0"}
