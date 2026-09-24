"""Drains the transactional outbox.

This is the piece that makes the outbox pattern real rather than aspirational:
services append `outbox_events` rows inside their business transaction (see
`app/platform/outbox.py`), and this task is the only thing that ever reads
them back out. Delivery is at-least-once — a handler that raises is retried
with backoff — so every handler here must be safe to run twice on the same
event.

Handlers are intentionally sparse. Most events this system emits today
(`project.created`, `site.created`, `unit_conversion.calibrated`, ...) have no
registered handler and are marked PROCESSED with nothing sent — that is
correct, not a gap: not every domain event has an obvious single recipient,
and the audit log already records all of them permanently. A handler is added
only when there is a real "who gets told, and what do they need to know".

Each event is processed in its own transaction, so one poisoned event cannot
block the rest of the batch, and a handler's own writes (a Notification row)
commit atomically with the outbox row's PROCESSED status.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import system_context
from app.core.db import SessionFactory, dispose_engine
from app.core.logging import get_logger
from app.core.types import utcnow
from app.modules.notifications.domain.enums import NotificationPriority, NotificationType
from app.modules.notifications.services import notification_service
from app.modules.vendors.services import vendor_service
from app.platform.domain.enums import OutboxStatus
from app.platform.models import OutboxEvent
from app.platform.outbox import MAX_ATTEMPTS, next_attempt_delay
from app.workers.celery_app import celery_app

log = get_logger("worker.outbox")

BATCH_SIZE = 50

Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}


def handler(event_type: str) -> Callable[[Handler], Handler]:
    """Register a handler for one event type."""

    def _register(func: Handler) -> Handler:
        _HANDLERS[event_type] = func
        return func

    return _register


# -----------------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------------


@handler("user.role_granted")
async def _notify_role_granted(session: AsyncSession, event: OutboxEvent) -> None:
    role_code = event.payload.get("role_code", "a role")
    scope_type = event.payload.get("scope_type", "")
    await notification_service.send(
        session,
        company_id=event.company_id,  # type: ignore[arg-type]
        user_id=event.aggregate_id,
        notification_type=NotificationType.ROLE_GRANTED.value,
        title="A new role was granted to you",
        body=f"You were granted {role_code} at {scope_type} scope.",
        entity_type="UserRoleGrant",
        entity_id=event.aggregate_id,
    )


async def _notify_vendor_status(session: AsyncSession, event: OutboxEvent, *, verb: str) -> None:
    creator_id = await vendor_service.get_creator_id(session, event.aggregate_id)
    if creator_id is None:
        return  # seeded/system-created vendor with no human creator to tell

    code = event.payload.get("code", "A vendor")
    reason = event.payload.get("reason")
    body = f"{code} was {verb}." + (f" Reason: {reason}" if reason else "")
    await notification_service.send(
        session,
        company_id=event.company_id,  # type: ignore[arg-type]
        user_id=creator_id,
        notification_type=NotificationType.VENDOR_STATUS_CHANGED.value,
        title=f"Vendor {verb}",
        body=body,
        priority=NotificationPriority.HIGH if verb != "approved" else NotificationPriority.NORMAL,
        entity_type="Vendor",
        entity_id=event.aggregate_id,
    )


@handler("vendor.approved")
async def _notify_vendor_approved(session: AsyncSession, event: OutboxEvent) -> None:
    await _notify_vendor_status(session, event, verb="approved")


@handler("vendor.suspended")
async def _notify_vendor_suspended(session: AsyncSession, event: OutboxEvent) -> None:
    await _notify_vendor_status(session, event, verb="suspended")


@handler("vendor.blacklisted")
async def _notify_vendor_blacklisted(session: AsyncSession, event: OutboxEvent) -> None:
    await _notify_vendor_status(session, event, verb="blacklisted")


# -----------------------------------------------------------------------------
# Drain loop
# -----------------------------------------------------------------------------


async def _claim_batch(session: AsyncSession) -> list[OutboxEvent]:
    """Lock a batch of due events, skipping any another worker already holds.

    `SKIP LOCKED` is what makes running more than one worker process safe:
    two workers draining concurrently partition the queue instead of racing
    for the same rows.
    """
    now = utcnow()
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.status.in_((OutboxStatus.PENDING.value, OutboxStatus.RETRYING.value)),
            OutboxEvent.next_attempt_at <= now,
        )
        .order_by(OutboxEvent.occurred_at)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _process_event(event_id: UUID) -> str:
    """Handle one event in its own transaction. Returns the outcome for
    logging: processed / retrying / dead / no-handler."""
    async with SessionFactory() as session:
        event = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
            )
        ).scalar_one_or_none()
        if event is None or event.status not in (
            OutboxStatus.PENDING.value,
            OutboxStatus.RETRYING.value,
        ):
            return "skipped"  # claimed and finished by another worker already

        event.status = OutboxStatus.PROCESSING.value
        event.attempts += 1
        # Committed, not merely flushed: the except branch below rolls back
        # on a handler failure, and a rollback undoes a flush just as much as
        # it undoes an uncommitted insert. Without this commit, the attempts
        # increment never survives a failure, `_record_failure` always reads
        # attempts back as 0, and `next_attempt_delay(0)` returns None —
        # every failing handler was going straight to DEAD on its first try
        # instead of ever retrying.
        await session.commit()

        handler_fn = _HANDLERS.get(event.event_type)
        if handler_fn is None:
            event.status = OutboxStatus.PROCESSED.value
            event.processed_at = utcnow()
            await session.commit()
            return "no-handler"

        try:
            with system_context("outbox-drain", event.company_id):
                await handler_fn(session, event)
            event.status = OutboxStatus.PROCESSED.value
            event.processed_at = utcnow()
            await session.commit()
            return "processed"
        except Exception as exc:  # noqa: BLE001 — every handler failure must be caught here
            await session.rollback()
            return await _record_failure(session, event_id, exc)


async def _record_failure(session: AsyncSession, event_id: UUID, exc: Exception) -> str:
    """A handler raised. Recorded in a fresh transaction, since the one that
    failed was just rolled back."""
    event = (
        await session.execute(
            select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
        )
    ).scalar_one()

    delay = next_attempt_delay(event.attempts)
    event.last_error = f"{type(exc).__name__}: {exc}"[:2000]

    if delay is None or event.attempts >= MAX_ATTEMPTS:
        event.status = OutboxStatus.DEAD.value
        log.error(
            "outbox.event_dead",
            event_id=str(event.id),
            event_type=event.event_type,
            attempts=event.attempts,
            error=event.last_error,
        )
        await session.commit()
        return "dead"

    event.status = OutboxStatus.RETRYING.value
    event.next_attempt_at = utcnow() + delay
    log.warning(
        "outbox.event_retry",
        event_id=str(event.id),
        event_type=event.event_type,
        attempts=event.attempts,
        next_attempt_at=event.next_attempt_at.isoformat(),
        error=event.last_error,
    )
    await session.commit()
    return "retrying"


async def _drain_once() -> dict[str, int]:
    outcomes: dict[str, int] = {}
    try:
        async with SessionFactory() as session:
            batch = await _claim_batch(session)
            event_ids = [event.id for event in batch]
            # Release the claim-batch transaction's lock immediately: each
            # event is then processed in its own short transaction (see
            # _process_event), so a slow handler never holds the row lock
            # for the whole batch.
            await session.commit()

        for event_id in event_ids:
            outcome = await _process_event(event_id)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
    finally:
        # `drain()` below wraps this whole coroutine in its own
        # asyncio.run(), which spins up a brand-new event loop on every
        # Celery-beat tick and tears it down again on return. The engine in
        # app.core.db is a single module-level singleton (correct for the
        # API's one long-lived loop), so its pooled asyncpg connections get
        # bound to whichever loop happened to create them. Without this,
        # a connection opened on tick N's loop survives in the pool and gets
        # handed to tick N+1's *different* loop, and asyncpg raises "got
        # Future ... attached to a different loop". Disposing here, still
        # inside the loop that owns these connections, empties the pool so
        # the next tick always opens fresh ones on its own new loop.
        await dispose_engine()

    return outcomes


@celery_app.task(name="outbox.drain")
def drain() -> dict[str, Any]:
    """Celery beat calls this roughly every second (see celery_app.py)."""
    outcomes = asyncio.run(_drain_once())
    if outcomes:
        log.info("outbox.drain_complete", **outcomes)
    return {"outcomes": outcomes}


@celery_app.task(name="outbox.retry_failed")
def retry_failed() -> dict[str, Any]:
    """A slower safety-net sweep. `drain` already retries on its own
    schedule; this exists for the rare case where beat itself missed a tick
    and RETRYING rows piled up with a past `next_attempt_at`."""
    result: dict[str, Any] = drain()
    return result
