"""Tests for multi-spec and team collaboration functionality."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.models import (
    Spec,
    SpecDraft,
    SpecStatus,
    Team,
    TeamMembership,
    TeamRole,
    WorkspaceTeam,
)

from .factories import (
    make_spec,
    make_spec_draft,
    make_team,
    make_team_membership,
    make_tenant,
    make_user,
    make_workspace,
    make_workspace_team,
)


class TestSpecModel:
    """Tests for Spec model constraints."""

    async def test_spec_name_version_unique_per_workspace(self, session: AsyncSession) -> None:
        """Spec name+version should be unique within a workspace."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        ws1 = make_workspace(tenant=tenant, name="Workspace 1")
        ws2 = make_workspace(tenant=tenant, name="Workspace 2")
        session.add_all([ws1, ws2])
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        # Same name+version in different workspaces should work
        spec1 = make_spec(workspace=ws1, created_by=user, name="MySpec", version="1.0")
        spec2 = make_spec(workspace=ws2, created_by=user, name="MySpec", version="1.0")
        session.add_all([spec1, spec2])
        await session.flush()

        # Same name+version in same workspace should fail
        spec3 = make_spec(workspace=ws1, created_by=user, name="MySpec", version="1.0")
        session.add(spec3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_spec_different_versions_allowed(self, session: AsyncSession) -> None:
        """Same spec name with different versions should be allowed."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec1 = make_spec(workspace=workspace, created_by=user, name="MySpec", version="1.0")
        spec2 = make_spec(workspace=workspace, created_by=user, name="MySpec", version="2.0")
        session.add_all([spec1, spec2])
        await session.flush()

        assert spec1.id != spec2.id

    async def test_spec_has_timestamps(self, session: AsyncSession) -> None:
        """Spec should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec = make_spec(workspace=workspace, created_by=user)
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

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec = make_spec(workspace=workspace, created_by=user)
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

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        # Test all status values
        for status in SpecStatus:
            spec = make_spec(
                workspace=workspace,
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

    async def test_draft_name_unique_per_workspace_user(self, session: AsyncSession) -> None:
        """Draft name should be unique per workspace+user combination."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user1 = make_user(tenant=tenant, email="user1@test.com")
        user2 = make_user(tenant=tenant, email="user2@test.com")
        session.add_all([user1, user2])
        await session.flush()

        # Same name by different users in same workspace should work
        draft1 = make_spec_draft(workspace=workspace, user=user1, name="MyDraft")
        draft2 = make_spec_draft(workspace=workspace, user=user2, name="MyDraft")
        session.add_all([draft1, draft2])
        await session.flush()

        # Same name by same user in same workspace should fail
        draft3 = make_spec_draft(workspace=workspace, user=user1, name="MyDraft")
        session.add(draft3)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_user_can_have_multiple_drafts(self, session: AsyncSession) -> None:
        """User can have multiple drafts with different names in same workspace."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        draft1 = make_spec_draft(workspace=workspace, user=user, name="Draft1")
        draft2 = make_spec_draft(workspace=workspace, user=user, name="Draft2")
        draft3 = make_spec_draft(workspace=workspace, user=user, name="Draft3")
        session.add_all([draft1, draft2, draft3])
        await session.flush()

        # Verify all drafts exist
        result = await session.execute(select(SpecDraft).where(SpecDraft.user_id == user.id))
        drafts = result.scalars().all()
        assert len(drafts) == 3

    async def test_draft_same_name_different_workspaces(self, session: AsyncSession) -> None:
        """Same draft name allowed in different workspaces."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        ws1 = make_workspace(tenant=tenant, name="Workspace 1")
        ws2 = make_workspace(tenant=tenant, name="Workspace 2")
        session.add_all([ws1, ws2])
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        draft1 = make_spec_draft(workspace=ws1, user=user, name="MyDraft")
        draft2 = make_spec_draft(workspace=ws2, user=user, name="MyDraft")
        session.add_all([draft1, draft2])
        await session.flush()

        assert draft1.id != draft2.id

    async def test_draft_has_timestamps(self, session: AsyncSession) -> None:
        """SpecDraft should have created_at and updated_at timestamps."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        draft = make_spec_draft(workspace=workspace, user=user)
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

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec = make_spec(workspace=workspace, created_by=user, name="OriginalSpec")
        session.add(spec)
        await session.flush()

        draft = make_spec_draft(
            workspace=workspace,
            user=user,
            name="EditingOriginalSpec",
            source_spec=spec,
        )
        session.add(draft)
        await session.flush()
        await session.refresh(draft)

        assert draft.source_spec_id == spec.id


class TestWorkspaceTeamModel:
    """Tests for WorkspaceTeam association model."""

    async def test_workspace_team_association(self, session: AsyncSession) -> None:
        """Team can be associated with workspace."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        team = make_team(tenant=tenant, name="Engineering")
        session.add_all([workspace, team])
        await session.flush()

        wt = make_workspace_team(workspace=workspace, team=team)
        session.add(wt)
        await session.flush()

        # Verify association
        result = await session.execute(
            select(WorkspaceTeam).where(
                WorkspaceTeam.workspace_id == workspace.id,
                WorkspaceTeam.team_id == team.id,
            )
        )
        assert result.scalar_one_or_none() is not None

    async def test_workspace_multiple_teams(self, session: AsyncSession) -> None:
        """Workspace can have multiple teams."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        team1 = make_team(tenant=tenant, name="Engineering")
        team2 = make_team(tenant=tenant, name="Research")
        session.add_all([workspace, team1, team2])
        await session.flush()

        wt1 = make_workspace_team(workspace=workspace, team=team1)
        wt2 = make_workspace_team(workspace=workspace, team=team2)
        session.add_all([wt1, wt2])
        await session.flush()

        result = await session.execute(
            select(WorkspaceTeam).where(WorkspaceTeam.workspace_id == workspace.id)
        )
        associations = result.scalars().all()
        assert len(associations) == 2

    async def test_team_multiple_workspaces(self, session: AsyncSession) -> None:
        """Team can access multiple workspaces."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        ws1 = make_workspace(tenant=tenant, name="Workspace 1")
        ws2 = make_workspace(tenant=tenant, name="Workspace 2")
        team = make_team(tenant=tenant, name="Engineering")
        session.add_all([ws1, ws2, team])
        await session.flush()

        wt1 = make_workspace_team(workspace=ws1, team=team)
        wt2 = make_workspace_team(workspace=ws2, team=team)
        session.add_all([wt1, wt2])
        await session.flush()

        result = await session.execute(
            select(WorkspaceTeam).where(WorkspaceTeam.team_id == team.id)
        )
        associations = result.scalars().all()
        assert len(associations) == 2

    async def test_workspace_team_unique(self, session: AsyncSession) -> None:
        """Same team cannot be added to workspace twice."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        team = make_team(tenant=tenant)
        session.add_all([workspace, team])
        await session.flush()

        wt1 = make_workspace_team(workspace=workspace, team=team)
        session.add(wt1)
        await session.flush()

        wt2 = make_workspace_team(workspace=workspace, team=team)
        session.add(wt2)

        with pytest.raises(IntegrityError):
            await session.flush()


class TestTeamBasedAccess:
    """Tests for team-based access control patterns."""

    async def test_user_accesses_workspace_via_team(self, session: AsyncSession) -> None:
        """User can access workspace through team membership."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        team = make_team(tenant=tenant, name="Research Team")
        session.add_all([workspace, team])
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        # Associate team with workspace
        wt = make_workspace_team(workspace=workspace, team=team)
        session.add(wt)
        await session.flush()

        # Add user to team
        membership = make_team_membership(user=user, team=team, role=TeamRole.MEMBER)
        session.add(membership)
        await session.flush()

        # Query workspaces accessible to user via teams
        result = await session.execute(
            select(WorkspaceTeam.workspace_id)
            .join(Team, WorkspaceTeam.team_id == Team.id)
            .join(TeamMembership, Team.id == TeamMembership.team_id)
            .where(TeamMembership.user_id == user.id)
        )
        accessible_workspace_ids = [row[0] for row in result.all()]

        assert workspace.id in accessible_workspace_ids

    async def test_admin_role_grants_edit_permission(self, session: AsyncSession) -> None:
        """Admin role in team should grant edit permission on workspace specs."""
        tenant = make_tenant()
        session.add(tenant)
        await session.flush()

        workspace = make_workspace(tenant=tenant)
        team = make_team(tenant=tenant)
        session.add_all([workspace, team])
        await session.flush()

        admin_user = make_user(tenant=tenant, email="admin@test.com")
        member_user = make_user(tenant=tenant, email="member@test.com")
        session.add_all([admin_user, member_user])
        await session.flush()

        wt = make_workspace_team(workspace=workspace, team=team)
        session.add(wt)
        await session.flush()

        admin_membership = make_team_membership(user=admin_user, team=team, role=TeamRole.ADMIN)
        member_membership = make_team_membership(user=member_user, team=team, role=TeamRole.MEMBER)
        session.add_all([admin_membership, member_membership])
        await session.flush()

        # Query to check admin role
        result = await session.execute(
            select(TeamMembership)
            .join(Team, TeamMembership.team_id == Team.id)
            .join(WorkspaceTeam, Team.id == WorkspaceTeam.team_id)
            .where(
                WorkspaceTeam.workspace_id == workspace.id,
                TeamMembership.user_id == admin_user.id,
                TeamMembership.role.in_([TeamRole.ADMIN, TeamRole.OWNER]),
            )
        )
        admin_access = result.scalar_one_or_none()
        assert admin_access is not None

        # Member should not have admin access
        result = await session.execute(
            select(TeamMembership)
            .join(Team, TeamMembership.team_id == Team.id)
            .join(WorkspaceTeam, Team.id == WorkspaceTeam.team_id)
            .where(
                WorkspaceTeam.workspace_id == workspace.id,
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

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec_data = {"entities": {"MyEntity": {"fields": []}}}
        draft = make_spec_draft(
            workspace=workspace,
            user=user,
            name="MySpec",
            version="1.0",
            spec_data=spec_data,
        )
        session.add(draft)
        await session.flush()

        # Simulate publishing: create spec from draft data
        spec = Spec(
            workspace_id=draft.workspace_id,
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

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        spec_data = {"entities": {"Original": {"fields": []}}}
        spec = make_spec(
            workspace=workspace,
            created_by=user,
            name="PublishedSpec",
            version="1.0",
            spec_data=spec_data,
        )
        session.add(spec)
        await session.flush()

        # Create draft from published spec
        draft = make_spec_draft(
            workspace=workspace,
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

        workspace = make_workspace(tenant=tenant)
        session.add(workspace)
        await session.flush()

        user = make_user(tenant=tenant)
        session.add(user)
        await session.flush()

        # Publish v1.0
        spec_v1 = make_spec(
            workspace=workspace,
            created_by=user,
            name="MySpec",
            version="1.0",
        )
        session.add(spec_v1)
        await session.flush()

        # Publish v2.0
        spec_v2 = make_spec(
            workspace=workspace,
            created_by=user,
            name="MySpec",
            version="2.0",
        )
        session.add(spec_v2)
        await session.flush()

        # Both versions should exist
        result = await session.execute(
            select(Spec).where(
                Spec.workspace_id == workspace.id,
                Spec.name == "MySpec",
            )
        )
        specs = result.scalars().all()
        assert len(specs) == 2
        versions = {s.version for s in specs}
        assert versions == {"1.0", "2.0"}
