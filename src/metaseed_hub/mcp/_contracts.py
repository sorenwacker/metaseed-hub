"""Collaborator types the MCP tool modules are registered with.

The profile resolver lives here rather than in each tool module because the
two had drifted: expressed as a bare ``Callable`` with three positional
parameters, it had no slot for the caller's tenant, so tools that wanted to
resolve a name collision in the caller's favour could not say so. Stating the
contract once, with the tenant in it, is what keeps every tool agreeing on
which specification a profile name denotes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ProfileSpecResolver(Protocol):
    """Resolves a profile name and version to a ``ProfileSpec``."""

    async def __call__(
        self,
        session: AsyncSession,
        profile: str,
        version: str,
        *,
        prefer_tenant: str | None = None,
    ) -> Any:
        """Return the spec for a profile version, built-in first, then published.

        Args:
            session: The database session to resolve published specs against.
            profile: The profile name.
            version: The profile version.
            prefer_tenant: Tenant whose publication wins a name and version
                collision. Omitting it falls back to the oldest publication
                across all tenants, which is rarely what a tool wants.

        Returns:
            The resolved ``ProfileSpec``.
        """
        ...
