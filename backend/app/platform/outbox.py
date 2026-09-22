"""Transactional outbox.

Services append events inside the business transaction. A Celery beat task
drains the table and dispatches to handlers. This is why:

* a notification can never be sent for a transaction that rolled back, and
* a committed transaction can never silently fail to notify.

Delivery is at-least-once, so every handler must be idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_context
from app.core.types import utcnow, uuid7
from app.platform.domain.enums import OutboxStatus
from app.platform.models import OutboxEvent

# Retry schedule for a handler that keeps failing. After the last step the
# event is marked DEAD and stays in the table as evidence that something did
# not happen — it is never deleted.
RETRY_BACKOFF: tuple[timedelta, ...] = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
)
MAX_ATTEMPTS = len(RETRY_BACKOFF) + 1


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """A fact about something that has happened, in the past tense.

    `event_type` is `aggregate.verb` — `delivery.approved`, `vendor_rate.changed`.
    The payload carries what a handler needs without re-reading the aggregate,
    but is not a substitute for it: handlers may load the current row.
    """

    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: dict[str, Any]
    company_id: UUID | None = None


async def emit(session: AsyncSession, event: DomainEvent) -> OutboxEvent:
    """Queue an event for delivery after this transaction commits."""
    ctx = current_context()
    row = OutboxEvent(
        id=uuid7(),
        company_id=event.company_id or ctx.company_id,
        event_type=event.event_type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        payload=event.payload,
        request_id=ctx.request_id if ctx.request_id != "-" else None,
        actor_user_id=ctx.user_id,
        occurred_at=utcnow(),
        status=OutboxStatus.PENDING.value,
        next_attempt_at=utcnow(),
    )
    session.add(row)
    return row


async def emit_many(session: AsyncSession, events: list[DomainEvent]) -> list[OutboxEvent]:
    return [await emit(session, event) for event in events]


def next_attempt_delay(attempts: int) -> timedelta | None:
    """Delay before the next retry, or None when retries are exhausted."""
    if attempts < 1 or attempts > len(RETRY_BACKOFF):
        return None
    return RETRY_BACKOFF[attempts - 1]
