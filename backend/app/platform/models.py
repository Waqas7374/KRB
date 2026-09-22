"""Platform infrastructure tables.

Not business entities — the machinery that makes business writes reliable:

* `outbox_events` — a committed transaction always produces its events, and a
  rolled-back one never does.
* `idempotency_keys` — a retried POST cannot create a second purchase order.
* `document_sequences` — gapless, per-company, per-fiscal-year numbering.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constraints import enum_check
from app.core.db import BaseModel
from app.platform.domain.enums import OutboxStatus


class OutboxEvent(BaseModel):
    """A domain event, written inside the business transaction.

    A Celery beat task drains this table, so delivery is at-least-once and
    handlers must be idempotent. `attempts` and `last_error` make a stuck
    handler visible instead of silent.
    """

    __tablename__ = "outbox_events"

    company_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Carried through so a notification raised by a background handler can still
    # be traced back to the request that caused it.
    request_id: Mapped[str | None] = mapped_column(String(64))
    actor_user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OutboxStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        enum_check("status", OutboxStatus),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The drain query: pending work that is due, oldest first.
        Index(
            "ix_outbox_events_pending",
            "next_attempt_at",
            "occurred_at",
            postgresql_where="status IN ('PENDING', 'RETRYING')",
        ),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )


class IdempotencyKey(BaseModel):
    """A stored response for a client-supplied `Idempotency-Key`.

    Same key + same body replays the stored response. Same key + different body
    is a 409 — the client changed its mind about a request the server may
    already have carried out, and guessing which one they meant is not the
    server's call.
    """

    __tablename__ = "idempotency_keys"

    company_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Set while the first request is still running, so two concurrent retries
    # cannot both execute the operation.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("key", "endpoint", name="uq_idempotency_keys_key_endpoint"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None


class DocumentSequence(BaseModel):
    """Gapless document numbering: PR-2026-00001, PO-2026-00184.

    Allocation takes a row lock rather than using a PostgreSQL sequence,
    because a sequence leaves gaps on rollback and auditors ask about gaps in
    purchase-order numbers.
    """

    __tablename__ = "document_sequences"

    company_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Fiscal year the counter belongs to, or 0 for a continuous sequence.
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    # Rendered into the number; e.g. "{prefix}-{year}-{value:0{padding}d}".
    year_token: Mapped[str | None] = mapped_column(String(10))
    next_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    padding: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "doc_type",
            "fiscal_year",
            name="uq_document_sequences_company_id_doc_type_fiscal_year",
        ),
        CheckConstraint("next_value >= 1", name="next_value_positive"),
        CheckConstraint("padding BETWEEN 1 AND 12", name="padding_range"),
    )


__all__ = ["DocumentSequence", "IdempotencyKey", "OutboxEvent"]
