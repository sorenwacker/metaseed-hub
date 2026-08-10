"""Factory functions for creating test model instances."""

from typing import Any
from uuid import uuid4

from metaseed_hub.models import (
    Dataset,
    Spec,
    SpecDraft,
    SpecStatus,
    Tenant,
    User,
)


def make_tenant(
    *,
    name: str = "Test Tenant",
    slug: str | None = None,
) -> Tenant:
    """Create a Tenant instance for testing.

    Args:
        name: Tenant display name.
        slug: URL-safe identifier. Auto-generated if not provided.

    Returns:
        Tenant model instance (not yet persisted).
    """
    return Tenant(
        name=name,
        slug=slug or f"tenant-{uuid4().hex[:8]}",
    )


def make_user(
    *,
    tenant: Tenant,
    email: str | None = None,
    keycloak_id: str | None = None,
    display_name: str = "Test User",
) -> User:
    """Create a User instance for testing.

    Args:
        tenant: Parent tenant for the user.
        email: User email address. Auto-generated if not provided.
        keycloak_id: Keycloak subject ID. Auto-generated if not provided.
        display_name: User display name.

    Returns:
        User model instance (not yet persisted).
    """
    suffix = uuid4().hex[:8]
    return User(
        tenant_id=tenant.id,
        email=email or f"user-{suffix}@example.com",
        keycloak_id=keycloak_id or f"kc-{suffix}",
        display_name=display_name,
    )


def make_dataset(
    *,
    tenant: Tenant,
    name: str | None = None,
    profile: str = "miappe",
    version: str = "1.1",
    data: dict | None = None,
) -> Dataset:
    """Create a Dataset instance for testing.

    Args:
        tenant: Parent tenant for the dataset.
        name: Dataset name. Auto-generated if not provided.
        profile: Metaseed profile type.
        version: Profile version.
        data: Optional JSONB data.

    Returns:
        Dataset model instance (not yet persisted).
    """
    return Dataset(
        tenant_id=tenant.id,
        name=name or f"Dataset {uuid4().hex[:8]}",
        profile=profile,
        version=version,
        data=data or {},
    )


def make_spec(
    *,
    tenant: Tenant,
    created_by: User,
    name: str | None = None,
    version: str = "1.0.0",
    description: str | None = None,
    spec_data: dict[str, Any] | None = None,
    status: SpecStatus = SpecStatus.PUBLISHED,
) -> Spec:
    """Create a Spec instance for testing.

    Args:
        tenant: Parent tenant for the spec.
        created_by: User who created the spec.
        name: Spec name. Auto-generated if not provided.
        version: Spec version.
        description: Optional description.
        spec_data: Optional spec data.
        status: Spec status.

    Returns:
        Spec model instance (not yet persisted).
    """
    return Spec(
        tenant_id=tenant.id,
        created_by_id=created_by.id,
        name=name or f"Spec{uuid4().hex[:8]}",
        version=version,
        description=description,
        spec_data=spec_data or {},
        status=status,
    )


def make_spec_draft(
    *,
    tenant: Tenant,
    user: User,
    name: str | None = None,
    version: str = "0.1",
    spec_data: dict[str, Any] | None = None,
    source_spec: Spec | None = None,
    template_source: str | None = None,
) -> SpecDraft:
    """Create a SpecDraft instance for testing.

    Args:
        tenant: Parent tenant for the draft.
        user: User who owns the draft.
        name: Draft name. Auto-generated if not provided.
        version: Draft version.
        spec_data: Optional spec data.
        source_spec: Optional source spec (for editing published specs).
        template_source: Optional template source identifier.

    Returns:
        SpecDraft model instance (not yet persisted).
    """
    return SpecDraft(
        tenant_id=tenant.id,
        user_id=user.id,
        source_spec_id=source_spec.id if source_spec else None,
        name=name or f"Draft{uuid4().hex[:8]}",
        version=version,
        spec_data=spec_data or {},
        template_source=template_source,
    )
