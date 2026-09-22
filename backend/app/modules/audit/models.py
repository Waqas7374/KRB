"""Audit log.

Append-only by database grant, not by convention: the application role is
denied UPDATE and DELETE on this table (see the migration). Every sensitive
operation writes a row here, with the old and new values, the actor, and the
device and address it came from.

This table is what makes §45 answerable — "who changed the rate, and from what
to what" is a query, not an investigation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constraints import enum_check
from app.core.db import Base
from app.core.types import uuid7
from app.modules.audit.domain.enums import AuditAction


class AuditLog(Base):
    """One recorded change.

    Deliberately not built on `BaseModel`: it has no `updated_at` (rows are
    never updated) and no `created_by_id` (the actor is `actor_user_id`, which
    may be null for system work and is then explained by `actor_label`).
    """

    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    company_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)

    # --- Who -----------------------------------------------------------------
    actor_user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    actor_name: Mapped[str | None] = mapped_column(String(160))
    # Set instead of a user id for seeds, migrations and scheduled jobs, so a
    # machine change is never mistaken for a human one.
    actor_label: Mapped[str | None] = mapped_column(String(80))
    actor_roles: Mapped[list[str] | None] = mapped_column(ARRAY(String(50)))

    # --- What ----------------------------------------------------------------
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # Human-readable identity of the row, so a deleted or renamed record is
    # still recognisable years later ("PO-2026-00184", "Shree Stone").
    entity_label: Mapped[str | None] = mapped_column(String(200))

    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    changed_fields: Mapped[list[str] | None] = mapped_column(ARRAY(String(80)))
    # A sentence an auditor can read without decoding JSON.
    summary: Mapped[str | None] = mapped_column(String(500))

    # --- Context -------------------------------------------------------------
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(400))
    device_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(64))
    # Dimensions copied from the audited row so the log can be filtered by
    # project or site without joining to a table that may have moved on.
    project_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    site_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        enum_check("action", AuditAction),
        # "What happened to this record?"
        Index("ix_audit_logs_entity", "entity_type", "entity_id", "occurred_at"),
        # "What did this person do?"
        Index("ix_audit_logs_actor", "actor_user_id", "occurred_at"),
        # "What happened on this project last month?"
        Index("ix_audit_logs_company_occurred", "company_id", "occurred_at"),
        Index("ix_audit_logs_project", "project_id", "occurred_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.entity_type}:{self.entity_id}>"


__all__ = ["AuditLog"]
