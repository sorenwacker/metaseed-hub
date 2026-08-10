"""Sharing: one mechanism for datasets, drafts and published specifications.

There were three. Datasets and specification drafts each had their own router,
their own template and their own two hundred lines saying the same thing in
different words, with different role names — a dataset had curators, a draft
had editors. Published specifications had a members table and nothing that
wrote to it, so handing one over meant editing the database by hand, which is
what had to be done when a colleague's specification needed a new owner.

This module holds the rules once. What differs between the three is only which
table a membership lives in and how the resource is loaded, which is what
:class:`SharedResource` describes; the routes and the interface are shared.

The roles are the same everywhere:

owner
    Full control: content, sharing, role changes, deletion. A resource always
    has at least one; the last one cannot be demoted, removed, or leave.
editor
    Changes the content, not who may see it.
viewer
    Reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from metaseed_hub.models import User


class Role(StrEnum):
    """What a member may do. One vocabulary for every shared thing."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


#: Roles that may change the content of a resource.
EDIT_ROLES = frozenset({Role.OWNER, Role.EDITOR})


class SharingError(Exception):
    """A share was refused, with a reason a person can act on."""


class NotAnOwnerError(SharingError):
    """Only owners may change who has access."""

    def __init__(self) -> None:
        super().__init__("Only an owner can change who has access.")


class NoSuchAccountError(SharingError):
    """The address named nobody who has ever signed in."""

    def __init__(self, email: str) -> None:
        super().__init__(
            f"No account here uses {email}. An account is created the first "
            "time someone signs in, so ask them to sign in once."
        )


class LastOwnerError(SharingError):
    """Removing or demoting the last owner would orphan the resource.

    An ownerless dataset cannot be shared, have its roles changed, or be
    deleted by anyone but an administrator, so this is refused rather than
    discovered later.
    """

    def __init__(self, action: str) -> None:
        super().__init__(f"This is the only owner. Make someone else an owner before you {action}.")


@dataclass(frozen=True)
class SharedResource:
    """How one kind of shared thing plugs into the shared rules.

    Attributes:
        kind: The word in the URL, and how a resource is named in messages.
        model: The resource's mapped class.
        member_model: The membership table's mapped class.
        foreign_key: Column on ``member_model`` naming the resource.
        title_of: The resource's human name, for messages.
    """

    kind: str
    model: type[Any]
    member_model: type[Any]
    foreign_key: str
    title_of: Any

    def owns_column(self) -> Any:
        return getattr(self.member_model, self.foreign_key)


def _resources() -> dict[str, SharedResource]:
    from metaseed_hub.models import (
        Dataset,
        DatasetMember,
        Spec,
        SpecDraft,
        SpecDraftMember,
        SpecMember,
    )

    return {
        "dataset": SharedResource(
            kind="dataset",
            model=Dataset,
            member_model=DatasetMember,
            foreign_key="dataset_id",
            title_of=lambda resource: resource.name,
        ),
        "draft": SharedResource(
            kind="draft",
            model=SpecDraft,
            member_model=SpecDraftMember,
            foreign_key="spec_draft_id",
            title_of=lambda resource: resource.name,
        ),
        "spec": SharedResource(
            kind="spec",
            model=Spec,
            member_model=SpecMember,
            foreign_key="spec_id",
            title_of=lambda resource: f"{resource.name} {resource.version}",
        ),
    }


def resource_for(kind: str) -> SharedResource:
    """The :class:`SharedResource` for ``kind``.

    Raises:
        KeyError: For an unknown kind, which a route turns into a 404 rather
            than trusting a path segment from the browser.
    """
    return _resources()[kind]


async def members_of(
    session: AsyncSession, resource: SharedResource, resource_id: str
) -> list[Any]:
    """Every membership of one resource, oldest first."""
    from metaseed_hub.models import User

    result = await session.execute(
        select(resource.member_model)
        .where(resource.owns_column() == resource_id)
        .join(User, resource.member_model.user_id == User.id)
        .order_by(resource.member_model.created_at)
    )
    return list(result.scalars().all())


async def role_of(
    session: AsyncSession, resource: SharedResource, resource_id: str, user_id: str
) -> Role | None:
    """``user_id``'s role in a resource, or ``None`` if they have no membership."""
    result = await session.execute(
        select(resource.member_model.role).where(
            resource.owns_column() == resource_id,
            resource.member_model.user_id == user_id,
        )
    )
    role = result.scalar_one_or_none()
    return Role(role) if role is not None else None


async def _owner_count(session: AsyncSession, resource: SharedResource, resource_id: str) -> int:
    members = await members_of(session, resource, resource_id)
    return sum(1 for member in members if Role(member.role) is Role.OWNER)


async def _require_owner(
    session: AsyncSession, resource: SharedResource, resource_id: str, user_id: str
) -> None:
    if await role_of(session, resource, resource_id, user_id) is not Role.OWNER:
        raise NotAnOwnerError


async def account_for_email(session: AsyncSession, email: str) -> User:
    """The account using ``email``.

    Matched without regard to capitalisation, and across the whole hub: sharing
    reaches people who are not in the sharer's own account.

    Raises:
        NoSuchAccountError: If nobody has signed in with that address.
    """
    from metaseed_hub.models import User

    result = await session.execute(
        select(User).where(User.email == email.strip().lower(), User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NoSuchAccountError(email.strip())
    return user


async def add_member(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    actor_id: str,
    email: str,
    role: Role = Role.VIEWER,
) -> Any:
    """Give the account using ``email`` a role, or change the one it has."""
    await _require_owner(session, resource, resource_id, actor_id)
    user = await account_for_email(session, email)

    existing = await session.get(resource.member_model, (resource_id, user.id))
    if existing is not None:
        return await set_role(
            session, resource, resource_id, actor_id=actor_id, user_id=user.id, role=role
        )

    member = resource.member_model(
        **{resource.foreign_key: resource_id}, user_id=user.id, role=role
    )
    session.add(member)
    await session.commit()
    return member


async def set_role(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    actor_id: str,
    user_id: str,
    role: Role,
) -> Any:
    """Change one member's role."""
    await _require_owner(session, resource, resource_id, actor_id)

    member = await session.get(resource.member_model, (resource_id, user_id))
    if member is None:
        raise SharingError("That person is not a member.")

    demoting_an_owner = Role(member.role) is Role.OWNER and role is not Role.OWNER
    if demoting_an_owner and await _owner_count(session, resource, resource_id) == 1:
        raise LastOwnerError("change this role")

    member.role = role
    await session.commit()
    return member


async def remove_member(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    actor_id: str,
    user_id: str,
) -> None:
    """Take away someone's access.

    Removing yourself is allowed — that is how a person leaves — but not if you
    are the only owner, whoever asks.
    """
    if actor_id != user_id:
        await _require_owner(session, resource, resource_id, actor_id)

    member = await session.get(resource.member_model, (resource_id, user_id))
    if member is None:
        return

    if Role(member.role) is Role.OWNER and await _owner_count(session, resource, resource_id) == 1:
        raise LastOwnerError("leave" if actor_id == user_id else "remove them")

    await session.delete(member)
    await session.commit()
