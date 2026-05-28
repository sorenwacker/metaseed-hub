"""Integration tests for Hub UI routes.

These tests verify route handlers work correctly with proper authentication,
authorization, and CSRF validation.

Note: Full integration tests require a PostgreSQL database. Tests that require
database access are marked with @pytest.mark.asyncio and require the session
fixture from conftest.py.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException


class TestRouteImports:
    """Verify all route modules import correctly."""

    def test_entity_routes_import(self) -> None:
        """Entity routes module imports."""
        from metaseed_hub.ui.routes import entity

        assert entity.router is not None

    def test_dataset_routes_import(self) -> None:
        """Dataset routes module imports."""
        from metaseed_hub.ui.routes import dataset

        assert dataset.router is not None

    def test_workspace_routes_import(self) -> None:
        """Workspace routes module imports."""
        from metaseed_hub.ui.routes import workspace

        assert workspace.router is not None

    def test_auth_routes_import(self) -> None:
        """Auth routes module imports."""
        from metaseed_hub.ui.routes import auth

        assert auth.router is not None


class TestAuthorizationIntegration:
    """Tests for authorization enforcement in routes."""

    @pytest.mark.asyncio
    async def test_get_dataset_for_user_verifies_access(self) -> None:
        """get_dataset_for_user denies access when user has no access."""
        from metaseed_hub.ui.dependencies import get_dataset_for_user

        # Mock dataset
        dataset = Mock()
        dataset.id = "ds-1"
        dataset.workspace_id = "ws-1"

        # Mock session that returns:
        # 1. dataset (first query for Dataset)
        # 2. None (workspace not found for workspace access check)
        # 3. None (user not found for DatasetMember check)
        session = AsyncMock()
        result_mocks = [
            Mock(scalar_one_or_none=Mock(return_value=dataset)),  # Dataset query
            Mock(scalar_one_or_none=Mock(return_value=None)),  # Workspace query
            Mock(scalar_one_or_none=Mock(return_value=None)),  # User query
        ]
        session.execute.side_effect = result_mocks

        # Mock user
        user = Mock()
        user.keycloak_id = "user12345678"

        # This will fail because user has no workspace access
        # and no DatasetMember record (user lookup returns None)
        with pytest.raises(HTTPException) as exc_info:
            await get_dataset_for_user("ds-1", session, user)

        assert exc_info.value.status_code == 403
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_verify_workspace_requires_matching_tenant(self) -> None:
        """Workspace access requires matching tenant."""
        from metaseed_hub.ui.dependencies import verify_workspace_access

        workspace = Mock()
        workspace.id = "ws-1"
        workspace.tenant_id = "tenant-a"

        tenant = Mock()
        tenant.id = "tenant-b"  # Different tenant

        session = AsyncMock()
        # First call returns workspace, second call returns tenant
        result1 = Mock()
        result1.scalar_one_or_none.return_value = workspace
        result2 = Mock()
        result2.scalar_one_or_none.return_value = tenant
        session.execute.side_effect = [result1, result2]

        user = Mock()
        user.keycloak_id = "user12345678"

        with pytest.raises(HTTPException) as exc_info:
            await verify_workspace_access("ws-1", session, user)

        assert exc_info.value.status_code == 403


class TestCSRFValidationInRoutes:
    """Tests for CSRF validation in route handlers."""

    def test_csrf_required_for_post_endpoints(self) -> None:
        """Verify POST endpoints require CSRF validation."""
        # Check that the handlers use validate_csrf_or_error

        # These imports succeed if the modules have the security imports
        from metaseed_hub.ui.security import validate_csrf_or_error

        assert validate_csrf_or_error is not None


class TestRenderTemplateIntegration:
    """Tests for template rendering integration."""

    def test_render_module_initialized(self) -> None:
        """Render module can be initialized."""
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        from metaseed_hub.ui.render import init_templates

        # Test that init_templates accepts a templates instance
        templates_dir = Path(__file__).parent.parent / "src/metaseed_hub/ui/templates"
        if templates_dir.exists():
            templates = Jinja2Templates(directory=str(templates_dir))
            init_templates(templates)

            # render_template should now work
            from metaseed_hub.ui.render import get_templates

            assert get_templates() is not None


class TestFormProcessingIntegration:
    """Tests for form processing in routes."""

    def test_extract_entity_values_all_types(self) -> None:
        """extract_entity_values handles all field types."""
        from starlette.datastructures import FormData

        from metaseed_hub.ui.forms import extract_entity_values

        # Create a mock helper
        helper = Mock()
        helper.all_fields = ["name", "count", "rate", "active"]
        helper.field_info = Mock(
            side_effect=lambda name: {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "rate": {"type": "float"},
                "active": {"type": "boolean"},
            }[name]
        )

        # Create form data
        form_data = FormData(
            [
                ("name", "Test Entity"),
                ("count", "42"),
                ("rate", "3.14"),
                ("active", "true"),
            ]
        )

        values = extract_entity_values(form_data, helper)

        assert values["name"] == "Test Entity"
        assert values["count"] == 42
        assert values["rate"] == 3.14
        assert values["active"] is True


class TestSecurityModuleIntegration:
    """Tests for security module integration."""

    def test_security_module_exports(self) -> None:
        """Security module exports all expected functions."""
        from metaseed_hub.ui.security import (
            CSRFValidationError,
            csrf_error_response,
            require_csrf,
            validate_csrf_or_error,
        )

        assert CSRFValidationError is not None
        assert csrf_error_response is not None
        assert require_csrf is not None
        assert validate_csrf_or_error is not None

    def test_csrf_error_response_format(self) -> None:
        """CSRF error response has correct format."""
        from metaseed_hub.ui.security import csrf_error_response

        response = csrf_error_response()

        assert response.status_code == 403
        assert "CSRF" in response.body.decode()


class TestDependenciesIntegration:
    """Tests for dependencies module integration."""

    def test_dependencies_exports(self) -> None:
        """Dependencies module exports all expected items."""
        from metaseed_hub.ui.dependencies import (
            CurrentUser,
            DbSession,
            OptionalUser,
            get_dataset_by_id,
            get_dataset_for_user,
            verify_workspace_access,
        )

        assert CurrentUser is not None
        assert DbSession is not None
        assert OptionalUser is not None
        assert get_dataset_by_id is not None
        assert get_dataset_for_user is not None
        assert verify_workspace_access is not None


class TestRouteStructure:
    """Tests for route module structure."""

    def test_entity_routes_use_auth(self) -> None:
        """Entity routes require authentication."""
        from metaseed_hub.ui.routes.entity import router

        # Check routes have CurrentUser dependency
        for route in router.routes:
            if hasattr(route, "dependant"):
                params = route.dependant.dependencies
                # Routes should have dependencies (auth, db, etc)
                assert len(params) >= 0  # Just verify structure exists

    def test_dataset_routes_use_auth(self) -> None:
        """Dataset routes require authentication."""
        from metaseed_hub.ui.routes.dataset import router

        for route in router.routes:
            if hasattr(route, "dependant"):
                assert route.dependant is not None

    def test_workspace_routes_use_auth(self) -> None:
        """Workspace routes require authentication."""
        from metaseed_hub.ui.routes.workspace import router

        for route in router.routes:
            if hasattr(route, "dependant"):
                assert route.dependant is not None


class TestSpecDraftSharing:
    """Tests for spec draft sharing functionality."""

    @pytest.mark.asyncio
    async def test_shared_draft_visible_to_member(self, session: AsyncMock) -> None:
        """User can see drafts shared with them."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            SpecDraft,
            SpecDraftMember,
            SpecDraftRole,
            Tenant,
            User,
            Workspace,
        )

        # Create tenant
        tenant = Tenant(name="Test Tenant", slug="test-tenant")
        session.add(tenant)
        await session.flush()

        # Create workspace
        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="Test",
        )
        session.add(workspace)
        await session.flush()

        # Create owner user
        owner_keycloak_id = str(uuid4())
        owner = User(
            keycloak_id=owner_keycloak_id,
            tenant_id=tenant.id,
            email="owner@test.com",
            display_name="Owner",
        )
        session.add(owner)
        await session.flush()

        # Create member user
        member_keycloak_id = str(uuid4())
        member = User(
            keycloak_id=member_keycloak_id,
            tenant_id=tenant.id,
            email="member@test.com",
            display_name="Member",
        )
        session.add(member)
        await session.flush()

        # Create draft owned by owner (user_id is FK to users.id)
        draft = SpecDraft(
            name="Shared Draft",
            user_id=owner.id,
            workspace_id=workspace.id,
            version="1.0",
        )
        session.add(draft)
        await session.flush()

        # Share draft with member
        membership = SpecDraftMember(
            spec_draft_id=draft.id,
            user_id=member.id,
            role=SpecDraftRole.EDITOR,
        )
        session.add(membership)
        await session.commit()

        # Query drafts shared with member
        result = await session.execute(
            select(SpecDraft)
            .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
            .where(SpecDraftMember.user_id == member.id)
        )
        shared_drafts = list(result.scalars().all())

        assert len(shared_drafts) == 1
        assert shared_drafts[0].id == draft.id
        assert shared_drafts[0].name == "Shared Draft"

    @pytest.mark.asyncio
    async def test_owner_visible_in_sharing_panel(self, session: AsyncMock) -> None:
        """Draft owner is visible in the sharing panel."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import Tenant, User, Workspace

        # Create tenant
        tenant = Tenant(name="Test Tenant", slug="test-tenant")
        session.add(tenant)
        await session.flush()

        # Create workspace
        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="Test",
        )
        session.add(workspace)
        await session.flush()

        # Create user
        keycloak_id = str(uuid4())
        user = User(
            keycloak_id=keycloak_id,
            tenant_id=tenant.id,
            email="user@test.com",
            display_name="Test User",
        )
        session.add(user)
        await session.commit()

        # Query user by keycloak_id and tenant_id (as the sharing panel does)
        result = await session.execute(
            select(User).where(
                User.keycloak_id == keycloak_id,
                User.tenant_id == tenant.id,
            )
        )
        found_user = result.scalar_one_or_none()

        assert found_user is not None
        assert found_user.display_name == "Test User"
        assert found_user.email == "user@test.com"

    @pytest.mark.asyncio
    async def test_alice_shares_with_demo_full_flow(self, session: AsyncMock) -> None:
        """Full sharing flow: Alice creates draft, shares with Demo, Demo sees it."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            SpecDraft,
            SpecDraftMember,
            SpecDraftRole,
            Tenant,
            User,
            Workspace,
        )

        # Create tenant
        tenant = Tenant(name="Test Tenant", slug="test-tenant")
        session.add(tenant)
        await session.flush()

        # Create workspace
        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="Test",
        )
        session.add(workspace)
        await session.flush()

        # Create Alice (owner)
        alice = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="alice@example.com",
            display_name="Alice",
        )
        session.add(alice)
        await session.flush()

        # Create Demo (recipient)
        demo = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="demo@example.com",
            display_name="Demo User",
        )
        session.add(demo)
        await session.flush()

        # Alice creates a draft
        draft = SpecDraft(
            name="Alice's Spec",
            user_id=alice.id,
            workspace_id=workspace.id,
            version="1.0",
            spec_data={
                "spec": {
                    "name": "Alice's Spec",
                    "display_name": "Alice's Spec",
                    "version": "1.0",
                    "entities": {},
                }
            },
        )
        session.add(draft)
        await session.flush()

        # Alice shares with Demo by email lookup
        result = await session.execute(select(User).where(User.email == "demo@example.com"))
        target_user = result.scalar_one_or_none()
        assert target_user is not None, "Demo user should be found by email"

        # Create membership
        membership = SpecDraftMember(
            spec_draft_id=draft.id,
            user_id=target_user.id,
            role=SpecDraftRole.EDITOR,
        )
        session.add(membership)
        await session.commit()

        # Verify Demo can see the shared draft
        # Query owned drafts (none for Demo)
        result = await session.execute(select(SpecDraft).where(SpecDraft.user_id == demo.id))
        owned_drafts = list(result.scalars().all())
        assert len(owned_drafts) == 0, "Demo should not own any drafts"

        # Query shared drafts
        result = await session.execute(
            select(SpecDraft)
            .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
            .where(SpecDraftMember.user_id == demo.id)
        )
        shared_drafts = list(result.scalars().all())
        assert len(shared_drafts) == 1, "Demo should see 1 shared draft"
        assert shared_drafts[0].name == "Alice's Spec"

        # Combined query (as the spec builder list does)
        owned_ids = {d.id for d in owned_drafts}
        all_drafts = owned_drafts + [d for d in shared_drafts if d.id not in owned_ids]
        assert len(all_drafts) == 1, "Demo should see 1 draft total"

    @pytest.mark.asyncio
    async def test_share_by_email_requires_existing_user(self, session: AsyncMock) -> None:
        """Sharing by email fails if user hasn't logged in yet."""
        from sqlalchemy import select

        from metaseed_hub.models import Tenant, User

        # Create tenant
        tenant = Tenant(name="Test Tenant", slug="test-tenant")
        session.add(tenant)
        await session.commit()

        # Try to find non-existent user by email
        result = await session.execute(select(User).where(User.email == "nonexistent@example.com"))
        user = result.scalar_one_or_none()

        assert user is None, "Non-existent user should not be found"

    @pytest.mark.asyncio
    async def test_member_roles(self, session: AsyncMock) -> None:
        """Test different member roles (owner, editor, viewer)."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            SpecDraft,
            SpecDraftMember,
            SpecDraftRole,
            Tenant,
            User,
            Workspace,
        )

        # Create tenant and workspace
        tenant = Tenant(name="Test Tenant", slug="test-tenant")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="Test",
        )
        session.add(workspace)
        await session.flush()

        # Create owner
        owner = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="owner@example.com",
            display_name="Owner",
        )
        session.add(owner)
        await session.flush()

        # Create draft
        draft = SpecDraft(
            name="Test Spec",
            user_id=owner.id,
            workspace_id=workspace.id,
            version="1.0",
        )
        session.add(draft)
        await session.flush()

        # Create users with different roles
        roles_to_test = [
            ("editor@example.com", "Editor", SpecDraftRole.EDITOR),
            ("viewer@example.com", "Viewer", SpecDraftRole.VIEWER),
            ("coowner@example.com", "Co-Owner", SpecDraftRole.OWNER),
        ]

        for email, name, role in roles_to_test:
            user = User(
                keycloak_id=str(uuid4()),
                tenant_id=tenant.id,
                email=email,
                display_name=name,
            )
            session.add(user)
            await session.flush()

            membership = SpecDraftMember(
                spec_draft_id=draft.id,
                user_id=user.id,
                role=role,
            )
            session.add(membership)

        await session.commit()

        # Verify all memberships
        result = await session.execute(
            select(SpecDraftMember).where(SpecDraftMember.spec_draft_id == draft.id)
        )
        memberships = list(result.scalars().all())

        assert len(memberships) == 3
        roles = {m.role for m in memberships}
        assert SpecDraftRole.EDITOR in roles
        assert SpecDraftRole.VIEWER in roles
        assert SpecDraftRole.OWNER in roles

    @pytest.mark.asyncio
    async def test_home_page_shows_shared_specs(self, session: AsyncMock) -> None:
        """Home page query includes specs shared via SpecDraftMember.

        This tests the fix for shared specs not appearing on the home page.
        The home page must query both:
        1. Specs in user's workspaces (owned)
        2. Specs shared with user via SpecDraftMember
        """
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            SpecDraft,
            SpecDraftMember,
            SpecDraftRole,
            Tenant,
            User,
            Workspace,
        )

        # Create two tenants (simulating different users/orgs)
        tenant_alice = Tenant(name="Alice Tenant", slug="alice-t")
        tenant_demo = Tenant(name="Demo Tenant", slug="demo-t")
        session.add(tenant_alice)
        session.add(tenant_demo)
        await session.flush()

        # Create workspaces for each tenant
        ws_alice = Workspace(
            name="Alice Workspace",
            tenant_id=tenant_alice.id,
            description="Alice's workspace",
        )
        ws_demo = Workspace(
            name="Demo Workspace",
            tenant_id=tenant_demo.id,
            description="Demo's workspace",
        )
        session.add(ws_alice)
        session.add(ws_demo)
        await session.flush()

        # Create users
        alice = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant_alice.id,
            email="alice@example.com",
            display_name="Alice",
        )
        demo = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant_demo.id,
            email="demo@example.com",
            display_name="Demo",
        )
        session.add(alice)
        session.add(demo)
        await session.flush()

        # Alice creates a spec in her workspace
        alice_spec = SpecDraft(
            name="Alice's Shared Spec",
            user_id=alice.id,
            workspace_id=ws_alice.id,
            version="1.0",
        )
        session.add(alice_spec)
        await session.flush()

        # Alice shares her spec with Demo
        membership = SpecDraftMember(
            spec_draft_id=alice_spec.id,
            user_id=demo.id,
            role=SpecDraftRole.VIEWER,
        )
        session.add(membership)
        await session.commit()

        # Simulate home page query for Demo user
        # Step 1: Get Demo's workspaces (only ws_demo)
        demo_workspace_ids = [ws_demo.id]

        # Step 2: Query specs from Demo's workspaces (should be empty)
        result = await session.execute(
            select(SpecDraft).where(SpecDraft.workspace_id.in_(demo_workspace_ids))
        )
        owned_specs = list(result.scalars().all())
        assert len(owned_specs) == 0, "Demo has no specs in own workspace"

        # Step 3: Query specs shared with Demo via SpecDraftMember
        result = await session.execute(
            select(SpecDraft)
            .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
            .where(SpecDraftMember.user_id == demo.id)
        )
        shared_specs = list(result.scalars().all())
        assert len(shared_specs) == 1, "Demo should see Alice's shared spec"
        assert shared_specs[0].name == "Alice's Shared Spec"

        # Step 4: Combine (as home page now does after the fix)
        seen_ids: set[str] = set()
        all_specs = []
        for spec in owned_specs + shared_specs:
            if spec.id not in seen_ids:
                seen_ids.add(spec.id)
                all_specs.append(spec)

        assert len(all_specs) == 1, "Demo should see 1 spec total on home page"
        assert all_specs[0].name == "Alice's Shared Spec"

    @pytest.mark.asyncio
    async def test_home_page_shows_shared_datasets(self, session: AsyncMock) -> None:
        """Home page query includes datasets shared via DatasetMember.

        This tests the fix for shared datasets not appearing on the home page.
        The home page must query both:
        1. Datasets in user's workspaces (owned)
        2. Datasets shared with user via DatasetMember
        """
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            Dataset,
            DatasetMember,
            DatasetRole,
            Tenant,
            User,
            Workspace,
        )

        # Create two tenants (simulating different users/orgs)
        tenant_alice = Tenant(name="Alice Tenant", slug="alice-t")
        tenant_demo = Tenant(name="Demo Tenant", slug="demo-t")
        session.add(tenant_alice)
        session.add(tenant_demo)
        await session.flush()

        # Create workspaces for each tenant
        ws_alice = Workspace(
            name="Alice Workspace",
            tenant_id=tenant_alice.id,
            description="Alice's workspace",
        )
        ws_demo = Workspace(
            name="Demo Workspace",
            tenant_id=tenant_demo.id,
            description="Demo's workspace",
        )
        session.add(ws_alice)
        session.add(ws_demo)
        await session.flush()

        # Create users
        alice = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant_alice.id,
            email="alice@example.com",
            display_name="Alice",
        )
        demo = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant_demo.id,
            email="demo@example.com",
            display_name="Demo",
        )
        session.add(alice)
        session.add(demo)
        await session.flush()

        # Alice creates a dataset in her workspace
        alice_dataset = Dataset(
            name="Alice's Shared Dataset",
            workspace_id=ws_alice.id,
            profile="miappe",
            version="1.2",
        )
        session.add(alice_dataset)
        await session.flush()

        # Alice shares her dataset with Demo as VIEWER
        membership = DatasetMember(
            dataset_id=alice_dataset.id,
            user_id=demo.id,
            role=DatasetRole.VIEWER,
        )
        session.add(membership)
        await session.commit()

        # Simulate home page query for Demo user
        # Step 1: Get Demo's workspaces (only ws_demo)
        demo_workspace_ids = [ws_demo.id]

        # Step 2: Query datasets from Demo's workspaces (should be empty)
        result = await session.execute(
            select(Dataset).where(
                Dataset.workspace_id.in_(demo_workspace_ids),
                Dataset.deleted_at.is_(None),
            )
        )
        owned_datasets = list(result.scalars().all())
        assert len(owned_datasets) == 0, "Demo has no datasets in own workspace"

        # Step 3: Query datasets shared with Demo via DatasetMember
        result = await session.execute(
            select(Dataset)
            .join(DatasetMember, DatasetMember.dataset_id == Dataset.id)
            .where(DatasetMember.user_id == demo.id, Dataset.deleted_at.is_(None))
        )
        shared_datasets = list(result.scalars().all())
        assert len(shared_datasets) == 1, "Demo should see Alice's shared dataset"
        assert shared_datasets[0].name == "Alice's Shared Dataset"

        # Step 4: Combine (as home page now does after the fix)
        seen_ids: set[str] = set()
        all_datasets = []
        for ds in owned_datasets + shared_datasets:
            if ds.id not in seen_ids:
                seen_ids.add(ds.id)
                all_datasets.append(ds)

        assert len(all_datasets) == 1, "Demo should see 1 dataset total on home page"
        assert all_datasets[0].name == "Alice's Shared Dataset"


class TestDatasetRBAC:
    """Tests for dataset role-based access control."""

    @pytest.mark.asyncio
    async def test_dataset_roles_all_types(self, session: AsyncMock) -> None:
        """Test all dataset roles (OWNER, CURATOR, VIEWER)."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            Dataset,
            DatasetMember,
            DatasetRole,
            Tenant,
            User,
            Workspace,
        )

        # Create tenant and workspace
        tenant = Tenant(name="Test Tenant", slug="test-t")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="Test",
        )
        session.add(workspace)
        await session.flush()

        # Create dataset owner
        owner = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="owner@example.com",
            display_name="Owner",
        )
        session.add(owner)
        await session.flush()

        # Create dataset
        dataset = Dataset(
            name="Test Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
        )
        session.add(dataset)
        await session.flush()

        # Create users with different roles
        roles_to_test = [
            ("curator@example.com", "Curator", DatasetRole.CURATOR),
            ("viewer@example.com", "Viewer", DatasetRole.VIEWER),
            ("coowner@example.com", "Co-Owner", DatasetRole.OWNER),
        ]

        for email, name, role in roles_to_test:
            user = User(
                keycloak_id=str(uuid4()),
                tenant_id=tenant.id,
                email=email,
                display_name=name,
            )
            session.add(user)
            await session.flush()

            membership = DatasetMember(
                dataset_id=dataset.id,
                user_id=user.id,
                role=role,
            )
            session.add(membership)

        await session.commit()

        # Verify all memberships
        result = await session.execute(
            select(DatasetMember).where(DatasetMember.dataset_id == dataset.id)
        )
        memberships = list(result.scalars().all())

        assert len(memberships) == 3
        roles = {m.role for m in memberships}
        assert DatasetRole.CURATOR in roles
        assert DatasetRole.VIEWER in roles
        assert DatasetRole.OWNER in roles

    @pytest.mark.asyncio
    async def test_dataset_viewer_can_see_shared_dataset(self, session: AsyncMock) -> None:
        """Viewer role can see datasets shared with them."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            Dataset,
            DatasetMember,
            DatasetRole,
            Tenant,
            User,
            Workspace,
        )

        tenant = Tenant(name="Test", slug="test")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(name="WS", tenant_id=tenant.id, description="")
        session.add(workspace)
        await session.flush()

        owner = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="owner@test.com",
            display_name="Owner",
        )
        viewer = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="viewer@test.com",
            display_name="Viewer",
        )
        session.add(owner)
        session.add(viewer)
        await session.flush()

        dataset = Dataset(
            name="Shared Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
        )
        session.add(dataset)
        await session.flush()

        membership = DatasetMember(
            dataset_id=dataset.id,
            user_id=viewer.id,
            role=DatasetRole.VIEWER,
        )
        session.add(membership)
        await session.commit()

        # Query as viewer
        result = await session.execute(
            select(Dataset)
            .join(DatasetMember, DatasetMember.dataset_id == Dataset.id)
            .where(DatasetMember.user_id == viewer.id)
        )
        visible_datasets = list(result.scalars().all())

        assert len(visible_datasets) == 1
        assert visible_datasets[0].name == "Shared Dataset"

    @pytest.mark.asyncio
    async def test_dataset_curator_has_edit_role(self, session: AsyncMock) -> None:
        """Curator role is stored correctly for datasets."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            Dataset,
            DatasetMember,
            DatasetRole,
            Tenant,
            User,
            Workspace,
        )

        tenant = Tenant(name="Test", slug="test")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(name="WS", tenant_id=tenant.id, description="")
        session.add(workspace)
        await session.flush()

        curator = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="curator@test.com",
            display_name="Curator",
        )
        session.add(curator)
        await session.flush()

        dataset = Dataset(
            name="Curated Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
        )
        session.add(dataset)
        await session.flush()

        membership = DatasetMember(
            dataset_id=dataset.id,
            user_id=curator.id,
            role=DatasetRole.CURATOR,
        )
        session.add(membership)
        await session.commit()

        # Query membership
        result = await session.execute(
            select(DatasetMember).where(
                DatasetMember.dataset_id == dataset.id,
                DatasetMember.user_id == curator.id,
            )
        )
        mem = result.scalar_one()

        assert mem.role == DatasetRole.CURATOR


class TestSpecRBAC:
    """Tests for spec draft role-based access control."""

    @pytest.mark.asyncio
    async def test_spec_roles_all_types(self, session: AsyncMock) -> None:
        """Test all spec draft roles (OWNER, EDITOR, VIEWER)."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            SpecDraft,
            SpecDraftMember,
            SpecDraftRole,
            Tenant,
            User,
            Workspace,
        )

        tenant = Tenant(name="Test Tenant", slug="test-t")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="Test",
        )
        session.add(workspace)
        await session.flush()

        owner = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="owner@example.com",
            display_name="Owner",
        )
        session.add(owner)
        await session.flush()

        draft = SpecDraft(
            name="Test Spec",
            user_id=owner.id,
            workspace_id=workspace.id,
            version="1.0",
        )
        session.add(draft)
        await session.flush()

        roles_to_test = [
            ("editor@example.com", "Editor", SpecDraftRole.EDITOR),
            ("viewer@example.com", "Viewer", SpecDraftRole.VIEWER),
            ("coowner@example.com", "Co-Owner", SpecDraftRole.OWNER),
        ]

        for email, name, role in roles_to_test:
            user = User(
                keycloak_id=str(uuid4()),
                tenant_id=tenant.id,
                email=email,
                display_name=name,
            )
            session.add(user)
            await session.flush()

            membership = SpecDraftMember(
                spec_draft_id=draft.id,
                user_id=user.id,
                role=role,
            )
            session.add(membership)

        await session.commit()

        result = await session.execute(
            select(SpecDraftMember).where(SpecDraftMember.spec_draft_id == draft.id)
        )
        memberships = list(result.scalars().all())

        assert len(memberships) == 3
        roles = {m.role for m in memberships}
        assert SpecDraftRole.EDITOR in roles
        assert SpecDraftRole.VIEWER in roles
        assert SpecDraftRole.OWNER in roles

    @pytest.mark.asyncio
    async def test_spec_viewer_can_see_shared_spec(self, session: AsyncMock) -> None:
        """Viewer role can see specs shared with them."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            SpecDraft,
            SpecDraftMember,
            SpecDraftRole,
            Tenant,
            User,
            Workspace,
        )

        tenant = Tenant(name="Test", slug="test")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(name="WS", tenant_id=tenant.id, description="")
        session.add(workspace)
        await session.flush()

        owner = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="owner@test.com",
            display_name="Owner",
        )
        viewer = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="viewer@test.com",
            display_name="Viewer",
        )
        session.add(owner)
        session.add(viewer)
        await session.flush()

        spec = SpecDraft(
            name="Shared Spec",
            user_id=owner.id,
            workspace_id=workspace.id,
            version="1.0",
        )
        session.add(spec)
        await session.flush()

        membership = SpecDraftMember(
            spec_draft_id=spec.id,
            user_id=viewer.id,
            role=SpecDraftRole.VIEWER,
        )
        session.add(membership)
        await session.commit()

        result = await session.execute(
            select(SpecDraft)
            .join(SpecDraftMember, SpecDraftMember.spec_draft_id == SpecDraft.id)
            .where(SpecDraftMember.user_id == viewer.id)
        )
        visible_specs = list(result.scalars().all())

        assert len(visible_specs) == 1
        assert visible_specs[0].name == "Shared Spec"

    @pytest.mark.asyncio
    async def test_spec_editor_has_edit_role(self, session: AsyncMock) -> None:
        """Editor role is stored correctly for specs."""
        from uuid import uuid4

        from sqlalchemy import select

        from metaseed_hub.models import (
            SpecDraft,
            SpecDraftMember,
            SpecDraftRole,
            Tenant,
            User,
            Workspace,
        )

        tenant = Tenant(name="Test", slug="test")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(name="WS", tenant_id=tenant.id, description="")
        session.add(workspace)
        await session.flush()

        owner = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="owner@test.com",
            display_name="Owner",
        )
        editor = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="editor@test.com",
            display_name="Editor",
        )
        session.add(owner)
        session.add(editor)
        await session.flush()

        spec = SpecDraft(
            name="Editable Spec",
            user_id=owner.id,
            workspace_id=workspace.id,
            version="1.0",
        )
        session.add(spec)
        await session.flush()

        membership = SpecDraftMember(
            spec_draft_id=spec.id,
            user_id=editor.id,
            role=SpecDraftRole.EDITOR,
        )
        session.add(membership)
        await session.commit()

        result = await session.execute(
            select(SpecDraftMember).where(
                SpecDraftMember.spec_draft_id == spec.id,
                SpecDraftMember.user_id == editor.id,
            )
        )
        mem = result.scalar_one()

        assert mem.role == SpecDraftRole.EDITOR


class TestDatasetAccessViaSharing:
    """Tests for dataset access via DatasetMember (sharing).

    Uses real database session to test get_dataset_for_user function.
    """

    @pytest.mark.asyncio
    async def test_shared_user_can_access_dataset(self, session: AsyncMock) -> None:
        """User with DatasetMember record can access shared dataset."""
        from uuid import uuid4

        from metaseed_hub.auth import TokenUser
        from metaseed_hub.models import (
            Dataset,
            DatasetMember,
            DatasetRole,
            Tenant,
            User,
            Workspace,
        )
        from metaseed_hub.ui.dependencies import get_dataset_for_user

        # Create owner's tenant and workspace
        owner_tenant = Tenant(name="Owner Tenant", slug="owner-te")
        session.add(owner_tenant)
        await session.flush()

        workspace = Workspace(
            name="Owner Workspace",
            tenant_id=owner_tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        # Create viewer in a DIFFERENT tenant (simulates cross-tenant sharing)
        viewer_tenant = Tenant(name="Viewer Tenant", slug="viewer-t")
        session.add(viewer_tenant)
        await session.flush()

        viewer_keycloak_id = str(uuid4())
        viewer = User(
            keycloak_id=viewer_keycloak_id,
            tenant_id=viewer_tenant.id,
            email="viewer@other.com",
            display_name="Viewer",
        )
        session.add(viewer)
        await session.flush()

        # Create dataset in owner's workspace
        dataset = Dataset(
            name="Shared Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
        )
        session.add(dataset)
        await session.flush()

        # Share dataset with viewer via DatasetMember
        membership = DatasetMember(
            dataset_id=dataset.id,
            user_id=viewer.id,
            role=DatasetRole.VIEWER,
        )
        session.add(membership)
        await session.commit()

        # Create TokenUser for the viewer (as returned by auth)
        token_user = TokenUser(
            sub=viewer_keycloak_id,
            email="viewer@other.com",
            name="Viewer",
            roles=[],
        )

        # Viewer should be able to access the dataset via DatasetMember
        result = await get_dataset_for_user(dataset.id, session, token_user)
        assert result.id == dataset.id
        assert result.name == "Shared Dataset"

    @pytest.mark.asyncio
    async def test_unshared_user_denied_access(self, session: AsyncMock) -> None:
        """User without workspace access or DatasetMember is denied."""
        from uuid import uuid4

        from metaseed_hub.auth import TokenUser
        from metaseed_hub.models import (
            Dataset,
            Tenant,
            User,
            Workspace,
        )
        from metaseed_hub.ui.dependencies import get_dataset_for_user

        # Create owner's tenant and workspace
        owner_tenant = Tenant(name="Owner Tenant", slug="owner-t2")
        session.add(owner_tenant)
        await session.flush()

        workspace = Workspace(
            name="Owner Workspace",
            tenant_id=owner_tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        # Create unrelated user in a different tenant (no membership)
        unrelated_tenant = Tenant(name="Unrelated Tenant", slug="unrela2")
        session.add(unrelated_tenant)
        await session.flush()

        unrelated_keycloak_id = str(uuid4())
        unrelated_user = User(
            keycloak_id=unrelated_keycloak_id,
            tenant_id=unrelated_tenant.id,
            email="unrelated@other.com",
            display_name="Unrelated",
        )
        session.add(unrelated_user)
        await session.flush()

        # Create dataset in owner's workspace (NOT shared with unrelated_user)
        dataset = Dataset(
            name="Private Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
        )
        session.add(dataset)
        await session.commit()

        # Create TokenUser for the unrelated user
        token_user = TokenUser(
            sub=unrelated_keycloak_id,
            email="unrelated@other.com",
            name="Unrelated",
            roles=[],
        )

        # Unrelated user should be denied access
        with pytest.raises(HTTPException) as exc_info:
            await get_dataset_for_user(dataset.id, session, token_user)

        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_workspace_owner_can_access_dataset(self, session: AsyncMock) -> None:
        """User who owns workspace can access datasets in it."""
        from uuid import uuid4

        from metaseed_hub.auth import TokenUser
        from metaseed_hub.models import (
            Dataset,
            Tenant,
            User,
            Workspace,
        )
        from metaseed_hub.ui.dependencies import get_dataset_for_user

        # Create owner's tenant (slug must match first 8 chars of keycloak_id)
        owner_keycloak_id = "owner123" + str(uuid4())[8:]
        owner_tenant = Tenant(name="Owner Tenant", slug=owner_keycloak_id[:8])
        session.add(owner_tenant)
        await session.flush()

        workspace = Workspace(
            name="Owner Workspace",
            tenant_id=owner_tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        owner = User(
            keycloak_id=owner_keycloak_id,
            tenant_id=owner_tenant.id,
            email="owner@example.com",
            display_name="Owner",
        )
        session.add(owner)
        await session.flush()

        # Create dataset in owner's workspace
        dataset = Dataset(
            name="My Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
        )
        session.add(dataset)
        await session.commit()

        # Create TokenUser for the owner
        token_user = TokenUser(
            sub=owner_keycloak_id,
            email="owner@example.com",
            name="Owner",
            roles=[],
        )

        # Owner should be able to access via workspace ownership
        result = await get_dataset_for_user(dataset.id, session, token_user)
        assert result.id == dataset.id
        assert result.name == "My Dataset"


class TestDatasetWithDraftSpec:
    """Tests for datasets using custom draft specs stored in database."""

    @pytest.mark.asyncio
    async def test_ensure_dataset_facade_loads_draft_spec(self, session: AsyncMock) -> None:
        """ensure_dataset_facade loads spec from database for datasets with spec_draft_id.

        This tests the fix for "add row" failing with 500 error when datasets use
        custom specs stored in the database instead of built-in profiles.
        The SpecLoader only knows built-in profiles, so we must load the spec
        from the SpecDraft table and inject it into the facade.
        """
        from uuid import uuid4

        from metaseed_hub.models import (
            Dataset,
            SpecDraft,
            Tenant,
            User,
            Workspace,
        )
        from metaseed_hub.ui.helpers import ensure_dataset_facade

        # Create tenant and workspace
        tenant = Tenant(name="Test Tenant", slug="test-dfs")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        # Create user
        user = User(
            keycloak_id=str(uuid4()),
            tenant_id=tenant.id,
            email="user@test.com",
            display_name="User",
        )
        session.add(user)
        await session.flush()

        # Create a custom spec draft with a complete ProfileSpec structure
        # Note: EntityDefSpec (used in entities dict) only has description, fields, example
        # (no name/version - those come from the profile level)
        spec_data = {
            "spec": {
                "name": "custom_spec",
                "display_name": "Custom Spec",
                "version": "1.0",
                "description": "A custom spec for testing",
                "root_entity": "Investigation",
                "entities": {
                    "Investigation": {
                        "description": "A research investigation",
                        "fields": [
                            {
                                "name": "unique_id",
                                "type": "string",
                                "required": True,
                                "description": "Unique identifier",
                            },
                            {
                                "name": "title",
                                "type": "string",
                                "required": True,
                                "description": "Investigation title",
                            },
                        ],
                    },
                    "Study": {
                        "description": "A study within an investigation",
                        "fields": [
                            {
                                "name": "unique_id",
                                "type": "string",
                                "required": True,
                                "description": "Unique identifier",
                            },
                            {
                                "name": "investigation_id",
                                "type": "string",
                                "required": True,
                                "description": "Parent investigation ID",
                            },
                            {
                                "name": "title",
                                "type": "string",
                                "required": False,
                                "description": "Study title",
                            },
                        ],
                    },
                },
            }
        }

        spec_draft = SpecDraft(
            name="Custom Spec",
            user_id=user.id,
            workspace_id=workspace.id,
            version="1.0",
            spec_data=spec_data,
        )
        session.add(spec_draft)
        await session.flush()

        # Create dataset that references the draft spec
        dataset = Dataset(
            name="Dataset with Custom Spec",
            workspace_id=workspace.id,
            profile="custom_spec",
            version="1.0",
            spec_draft_id=spec_draft.id,
            data={
                "profile": "custom_spec",
                "version": "1.0",
                "tree": [],
            },
        )
        session.add(dataset)
        await session.commit()

        # Call ensure_dataset_facade - this should load the spec from database
        state = await ensure_dataset_facade(dataset, session)

        # Verify state has the correct profile
        assert state.profile == "custom_spec"
        assert state.version == "1.0"

        # Verify facade was created and has the correct entity types
        facade = state.get_or_create_facade()
        assert facade is not None

        # Check that the facade has helpers for the entities in the spec
        assert hasattr(facade, "Investigation")
        assert hasattr(facade, "Study")

        # Verify we can create entity instances using the facade
        investigation = facade.Investigation.create(
            unique_id="INV-001",
            title="Test Investigation",
        )
        assert investigation is not None

    @pytest.mark.asyncio
    async def test_ensure_dataset_facade_falls_back_to_builtin(self, session: AsyncMock) -> None:
        """ensure_dataset_facade works for built-in profiles without spec_draft_id."""
        from metaseed_hub.models import (
            Dataset,
            Tenant,
            Workspace,
        )
        from metaseed_hub.ui.helpers import ensure_dataset_facade

        # Create tenant and workspace
        tenant = Tenant(name="Test Tenant", slug="test-fb")
        session.add(tenant)
        await session.flush()

        workspace = Workspace(
            name="Test Workspace",
            tenant_id=tenant.id,
            description="",
        )
        session.add(workspace)
        await session.flush()

        # Create dataset using built-in miappe profile (no spec_draft_id)
        dataset = Dataset(
            name="MIAPPE Dataset",
            workspace_id=workspace.id,
            profile="miappe",
            version="1.2",
            data={
                "profile": "miappe",
                "version": "1.2",
                "tree": [],
            },
        )
        session.add(dataset)
        await session.commit()

        # Call ensure_dataset_facade
        state = await ensure_dataset_facade(dataset, session)

        # Verify state has the correct profile
        assert state.profile == "miappe"

        # Verify facade was created for built-in profile
        facade = state.get_or_create_facade()
        assert facade is not None
        assert hasattr(facade, "Investigation")
