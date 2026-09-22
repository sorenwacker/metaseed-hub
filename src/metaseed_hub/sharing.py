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

A collaboration grant gives everyone in an SRAM collaboration (or one of its
groups) the editor or viewer role on a resource, as a fallback behind every
per-person rule; :mod:`metaseed_hub.collaborations` says who is in one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import contains_eager

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
        grant_model: The collaboration grant table's mapped class.
        foreign_key: Column on ``member_model`` and ``grant_model`` naming the
            resource.
        title_of: The resource's human name, for messages.
    """

    kind: str
    model: type[Any]
    member_model: type[Any]
    grant_model: type[Any]
    foreign_key: str
    title_of: Any
    # The column naming who made the thing, where the model has one. Creation
    # writes no membership row, so without this its creator has no role and
    # every owner-only control — sharing included — is hidden from them.
    # Datasets have no such column: their ownership is the tenant plus the
    # membership table, and always has been.
    creator_column: str | None = None

    def owns_column(self) -> Any:
        return getattr(self.member_model, self.foreign_key)

    def grant_column(self) -> Any:
        return getattr(self.grant_model, self.foreign_key)

    def creator_of(self, resource: Any) -> str | None:
        """The id of whoever created ``resource``, if the model records one."""
        if self.creator_column is None:
            return None
        created_by: str | None = getattr(resource, self.creator_column, None)
        return created_by


def _resources() -> dict[str, SharedResource]:
    from metaseed_hub.models import (
        Dataset,
        DatasetCollaborationGrant,
        DatasetMember,
        Spec,
        SpecCollaborationGrant,
        SpecDraft,
        SpecDraftCollaborationGrant,
        SpecDraftMember,
        SpecMember,
    )

    return {
        "dataset": SharedResource(
            kind="dataset",
            model=Dataset,
            member_model=DatasetMember,
            grant_model=DatasetCollaborationGrant,
            foreign_key="dataset_id",
            title_of=lambda resource: resource.name,
        ),
        "draft": SharedResource(
            kind="draft",
            model=SpecDraft,
            member_model=SpecDraftMember,
            grant_model=SpecDraftCollaborationGrant,
            foreign_key="spec_draft_id",
            title_of=lambda resource: resource.name,
            creator_column="user_id",
        ),
        "spec": SharedResource(
            kind="spec",
            model=Spec,
            member_model=SpecMember,
            grant_model=SpecCollaborationGrant,
            creator_column="created_by_id",
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


async def may_see_members(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    user_id: str,
) -> bool:
    """Whether this person may see who has access to a resource.

    Anyone with a role: shared with it, its creator, or the person whose
    account it lives in. Without this the members list — names and email
    addresses — was readable by anyone signed in who knew an id, since only
    the write paths checked anything.
    """
    return await role_of(session, resource, resource_id, user_id) is not None


async def members_of(
    session: AsyncSession, resource: SharedResource, resource_id: str
) -> list[Any]:
    """Every membership of one resource, oldest first."""
    from metaseed_hub.models import User

    # contains_eager, not a bare join: the join only filtered, leaving
    # `member.user` a lazy relationship that the panel template dereferences
    # per row — a sync load on an AsyncSession, i.e. MissingGreenlet and a 500
    # for every member the request's identity map does not already hold.
    result = await session.execute(
        select(resource.member_model)
        .where(resource.owns_column() == resource_id)
        .join(User, resource.member_model.user_id == User.id)
        .options(contains_eager(resource.member_model.user))
        .order_by(resource.member_model.created_at)
    )
    return list(result.scalars().unique().all())


async def role_of(
    session: AsyncSession, resource: SharedResource, resource_id: str, user_id: str
) -> Role | None:
    """``user_id``'s role in a resource, or ``None`` if they have none.

    This is the one answer every layer reads: the web routes, the REST API,
    the MCP tools and the spec builder. Three rules, in order:

    1. An explicit membership row decides, so a creator given a lesser role
       keeps it.
    2. Failing that, whoever created the resource owns it, where the model
       records a creator.
    3. Failing that, the person whose account the resource lives in owns it.
       An account belongs to one person, so this is the resource's home.
    4. Failing that, a collaboration grant matching the person's recorded
       SRAM membership gives its role: the one group rule, and a fallback.
    """
    result = await session.execute(
        select(resource.member_model.role).where(
            resource.owns_column() == resource_id,
            resource.member_model.user_id == user_id,
        )
    )
    role = result.scalar_one_or_none()
    if role is not None:
        return Role(role)

    found = await session.get(resource.model, resource_id)
    if found is None:
        return None
    if resource.creator_column is not None and resource.creator_of(found) == user_id:
        return Role.OWNER
    holder = await account_owner(session, str(found.tenant_id))
    if holder is not None and holder.id == user_id:
        return Role.OWNER
    return await _granted_role(session, resource, resource_id, user_id)


#: Which of two granted roles counts when a person is in several granted groups.
_GRANT_RANK = {Role.VIEWER: 0, Role.EDITOR: 1}


async def _granted_role(
    session: AsyncSession, resource: SharedResource, resource_id: str, user_id: str
) -> Role | None:
    """The best role any collaboration grant gives ``user_id``, or None."""
    from metaseed_hub.collaborations import entitled_urns_of

    urns = await entitled_urns_of(session, user_id)
    if not urns:
        return None
    result = await session.execute(
        select(resource.grant_model.role).where(
            resource.grant_column() == resource_id, resource.grant_model.urn.in_(urns)
        )
    )
    roles = [Role(role) for role in result.scalars().all()]
    return max(roles, key=_GRANT_RANK.__getitem__) if roles else None


async def accessible_ids(session: AsyncSession, resource: SharedResource, user_id: str) -> set[str]:
    """Ids of every resource shared with ``user_id``: by membership or by grant.

    What a list page adds to the person's own account: the two ways a thing
    reaches someone whose account it does not live in.
    """
    from metaseed_hub.collaborations import entitled_urns_of

    ids = {
        str(found)
        for found in (
            await session.execute(
                select(resource.owns_column()).where(resource.member_model.user_id == user_id)
            )
        ).scalars()
    }
    urns = await entitled_urns_of(session, user_id)
    if urns:
        ids |= {
            str(found)
            for found in (
                await session.execute(
                    select(resource.grant_column()).where(resource.grant_model.urn.in_(urns))
                )
            ).scalars()
        }
    return ids


async def account_owner(session: AsyncSession, tenant_id: str) -> User | None:
    """The person whose account holds an item.

    An account belongs to exactly one person, so this is who to approach about
    a specification that lives there. It is not always the author: publishing a
    draft someone shared with you puts the specification in *their* account
    while recording you as its author.

    Args:
        session: Database session.
        tenant_id: The account.

    Returns:
        The owning user, or None if the account has no live user.
    """
    from metaseed_hub.models import User

    result = await session.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.deleted_at.is_(None))
        .order_by(User.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


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


async def record_creator(
    session: AsyncSession, resource: SharedResource, thing: Any, user_id: str | None
) -> None:
    """Make the person who created ``thing`` its first owner.

    Every creation path calls this: without a membership row the creator has no
    role, so the sharing panel showed them no controls and ``add_member``
    refused them. ``thing`` is the freshly added resource; its id is assigned
    on flush, so the session is flushed here. The membership is added, not
    committed: the caller commits the resource and its ownership together.
    ``user_id`` may be ``None`` only for callers that act without a person,
    which then leave the resource ownerless deliberately.
    """
    if user_id is None:
        return
    await session.flush()
    if await session.get(resource.member_model, (thing.id, user_id)) is not None:
        return
    session.add(
        resource.member_model(**{resource.foreign_key: thing.id}, user_id=user_id, role=Role.OWNER)
    )


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


class OwnerGrantError(SharingError):
    """A collaboration cannot own: ownership is what the last-owner rule guards."""

    def __init__(self) -> None:
        super().__init__("A collaboration can be an editor or a viewer, not an owner.")


async def grants_of(session: AsyncSession, resource: SharedResource, resource_id: str) -> list[Any]:
    """Every collaboration grant on one resource, oldest first."""
    result = await session.execute(
        select(resource.grant_model)
        .where(resource.grant_column() == resource_id)
        .order_by(resource.grant_model.created_at)
    )
    return list(result.scalars().all())


def _grantable(role: Role) -> Role:
    if role is Role.OWNER:
        raise OwnerGrantError
    return role


async def add_grant(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    actor_id: str,
    urn: str,
    role: Role,
) -> Any:
    """Give a collaboration or group a role, or change the one it has.

    The actor must own the resource and, as of their last sign-in, be in the
    collaboration: handing a thing to a group one cannot see is refused.

    Raises:
        NotAnOwnerError: If the actor does not own the resource.
        OwnerGrantError: If ``role`` is owner.
        metaseed_hub.collaborations.NotInCollaborationError: If the actor's
            snapshot does not put them in ``urn``.
    """
    from metaseed_hub.collaborations import NotInCollaborationError, entitled_urns_of

    await _require_owner(session, resource, resource_id, actor_id)
    _grantable(role)
    if urn not in await entitled_urns_of(session, actor_id):
        raise NotInCollaborationError(urn)
    existing = (
        await session.execute(
            select(resource.grant_model).where(
                resource.grant_column() == resource_id, resource.grant_model.urn == urn
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.role = role
        await session.commit()
        return existing
    grant = resource.grant_model(**{resource.foreign_key: resource_id}, urn=urn, role=role)
    session.add(grant)
    await session.commit()
    return grant


async def _owned_grant(
    session: AsyncSession, resource: SharedResource, resource_id: str, grant_id: str
) -> Any:
    grant = await session.get(resource.grant_model, grant_id)
    if grant is None or str(getattr(grant, resource.foreign_key)) != str(resource_id):
        raise SharingError("That collaboration has no access here.")
    return grant


async def set_grant_role(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    actor_id: str,
    grant_id: str,
    role: Role,
) -> Any:
    """Change what one collaboration grant allows."""
    await _require_owner(session, resource, resource_id, actor_id)
    grant = await _owned_grant(session, resource, resource_id, grant_id)
    grant.role = _grantable(role)
    await session.commit()
    return grant


async def remove_grant(
    session: AsyncSession,
    resource: SharedResource,
    resource_id: str,
    *,
    actor_id: str,
    grant_id: str,
) -> None:
    """Take a collaboration's access away."""
    await _require_owner(session, resource, resource_id, actor_id)
    grant = await _owned_grant(session, resource, resource_id, grant_id)
    await session.delete(grant)
    await session.commit()
