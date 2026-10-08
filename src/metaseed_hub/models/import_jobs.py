"""Repository imports that run in the background.

A job is one submission of the New Dataset screen's repository tab: the
identifiers asked for, in order, and the outcome of each as it is reached. The
row is what the page polls and what a person returning later finds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ImportJob(Base):
    """One run of the repository import, owned by the person who started it."""

    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_jobs_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False
    )
    profile: Mapped[str] = mapped_column(String(100), nullable=False)
    identifiers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    """What was asked for, in the order it is fetched."""
    outcomes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    """One entry per identifier reached so far: identifier, status, dataset_id, name, detail."""
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    """``running``, ``done``, or ``interrupted`` by a hub restart."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
