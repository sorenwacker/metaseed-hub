"""Who a token acts as.

A client holding only a personal access token — a metaseed instance pushing
to the hub — cannot know the tenant the token belongs to, and the dataset
routes need one. This is the one call that tells it, and doubles as the
connection check.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from metaseed_hub.access import get_tenant_for_user
from metaseed_hub.auth import TokenUser, get_current_user
from metaseed_hub.database import get_session

router = APIRouter()


class MeResponse(BaseModel):
    """The account and tenant behind the presented credential."""

    email: str
    name: str
    tenant_id: str
    tenant_name: str


@router.get("", response_model=MeResponse)
async def me(
    user: Annotated[TokenUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """The account and tenant the token acts in.

    Raises:
        HTTPException: 403 when the account has no tenant on this hub.
    """
    tenant = await get_tenant_for_user(session, user)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No hub account")
    return MeResponse(
        email=user.email, name=user.name, tenant_id=tenant.id, tenant_name=tenant.name
    )
