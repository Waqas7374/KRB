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

from app.core.config import settings
from app.core.context import system_context
from app.core.db import SessionFactory, dispose_engine
from app.core.logging import get_logger
from app.core.types import utcnow
from app.modules.access.services import approver_lookup
from app.modules.notifications.domain.enums import NotificationPriority, NotificationType
from app.modules.notifications.services import notification_service
from app.modules.vendors.services import vendor_service
from app.platform.domain.enums import OutboxStatus
from app.platform.mailer import send_email
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


_LINK_COPY = {
    "invite": (
        "You have been invited to KRB ERP",
        "An account has been created for you. Open this link to choose your password",
    ),
    "reset": (
        "Reset your KRB ERP password",
        "Someone (hopefully you) asked to reset your password. Open this link to choose a new one",
    ),
    "admin_reset": (
        "Your KRB ERP password was reset",
        "An administrator reset your password. Open this link to choose a new one",
    ),
}


@handler("auth.password_link_issued")
async def _email_password_link(session: AsyncSession, event: OutboxEvent) -> None:
    """Email an invitation or password-reset link, then scrub the token.

    The clear token has to be in the payload to reach the email, but it must
    not sit in the outbox table afterwards: anyone who can read that table
    could otherwise use it. It is removed in the same transaction that marks
    the event PROCESSED. If sending keeps failing the token stays until the
    event is DEAD — by then the link has expired anyway (30 minutes for a
    reset, `invite_link_ttl_hours` for an invite).

    Phone-only accounts (site staff) get no email; SMS delivery is deferred
    (docs/10 backlog), so an administrator must relay the reset for them.
    """
    payload = dict(event.payload)
    token = payload.get("token")
    email = payload.get("email")
    if not token:
        return  # already scrubbed: a redelivery after a successful send
    if email:
        subject, lead = _LINK_COPY.get(str(payload.get("purpose")), _LINK_COPY["reset"])
        link = f"{settings.web_base_url.rstrip('/')}/reset-password?token={token}"
        body = (
            f"Hello {payload.get('full_name', '')},\n\n{lead}:\n\n{link}\n\n"
            f"The link can be used once and expires at {payload.get('expires_at')} (UTC).\n"
            "If you did not expect this email, you can ignore it.\n"
        )
        # smtplib is blocking; keep it off the event loop.
        await asyncio.to_thread(send_email, to=str(email), subject=subject, body=body)
    else:
        log.warning("outbox.password_link_no_email", user_id=str(event.aggregate_id))
    payload.pop("token", None)
    payload["delivered"] = bool(email)
    event.payload = payload


def _doc_label(event: OutboxEvent) -> str:
    kind = str(event.payload.get("doc_type", "document")).replace("_", " ")
    number = event.payload.get("doc_number")
    return f"{kind} {number}" if number else kind


async def _notify_users(
    session: AsyncSession,
    event: OutboxEvent,
    user_ids: list[str],
    *,
    kind: NotificationType,
    title: str,
    body: str,
    priority: NotificationPriority = NotificationPriority.NORMAL,
) -> None:
    for raw in dict.fromkeys(user_ids):  # de-duplicate, keep order
        await notification_service.send(
            session,
            company_id=event.company_id,  # type: ignore[arg-type]
            user_id=UUID(raw),
            notification_type=kind.value,
            title=title,
            body=body,
            priority=priority,
            entity_type="ApprovalRequest",
            entity_id=event.aggregate_id,
            link_path=event.payload.get("link_path"),
        )


@handler("approval.step_activated")
async def _notify_approvers(session: AsyncSession, event: OutboxEvent) -> None:
    label = _doc_label(event)
    await _notify_users(
        session,
        event,
        list(event.payload.get("approver_ids") or []),
        kind=NotificationType.APPROVAL_PENDING,
        title=f"Approval needed: {label}",
        body=f"{event.payload.get('summary') or label} is waiting for you "
        f"({event.payload.get('step_name')}).",
    )


@handler("approval.escalated")
async def _notify_escalation(session: AsyncSession, event: OutboxEvent) -> None:
    label = _doc_label(event)
    await _notify_users(
        session,
        event,
        list(event.payload.get("approver_ids") or []),
        kind=NotificationType.APPROVAL_ESCALATED,
        title=f"Overdue approval: {label}",
        body=f"{event.payload.get('step_name')} for {label} is past its deadline.",
        priority=NotificationPriority.HIGH,
    )


@handler("approval.reminder")
async def _notify_reminder(session: AsyncSession, event: OutboxEvent) -> None:
    label = _doc_label(event)
    left = event.payload.get("hours_left")
    await _notify_users(
        session,
        event,
        list(event.payload.get("approver_ids") or []),
        kind=NotificationType.APPROVAL_REMINDER,
        title=f"Reminder: {label} is waiting for you",
        body=f"{event.payload.get('step_name')} for {label} has used "
        f"{event.payload.get('percent')}% of its time"
        + (f"; about {left}h remain." if left is not None else "."),
    )


@handler("approval.integrity_alarm")
async def _notify_integrity_alarm(session: AsyncSession, event: OutboxEvent) -> None:
    """Tell the people who can act on drift: the super administrators. A
    mismatch between a document and its approval is a data fix, not a business
    decision."""
    admins = await approver_lookup.users_holding_role(
        session,
        company_id=event.company_id,  # type: ignore[arg-type]
        role_code="SUPER_ADMIN",
        project_id=None,
        site_id=None,
        department_id=None,
        on=utcnow().date(),
    )
    findings = event.payload.get("findings") or []
    for user_id in sorted(admins, key=str):
        await notification_service.send(
            session,
            company_id=event.company_id,  # type: ignore[arg-type]
            user_id=user_id,
            notification_type=NotificationType.APPROVAL_INTEGRITY_ALARM.value,
            title=f"Approval integrity check found {event.payload.get('count')} problem(s)",
            body=findings[0]["detail"] if findings else "",
            priority=NotificationPriority.URGENT,
            entity_type="Approvals",
            entity_id=event.aggregate_id,
            link_path="/approvals",
        )


@handler("delivery.received")
@handler("delivery.corrected")
async def _notify_reviewers(session: AsyncSession, event: OutboxEvent) -> None:
    """A delivery that needs a person's attention: tell the people who may review
    it at that site or project — not the person who captured it, and not the
    super administrator (whose grant covers everything and would hear of every
    delivery everywhere)."""
    payload = event.payload
    if not payload.get("needs_review"):
        return
    site_id = UUID(payload["site_id"]) if payload.get("site_id") else None
    project_id = UUID(payload["project_id"]) if payload.get("project_id") else None
    reviewers = await approver_lookup.users_with_permission(
        session,
        company_id=event.company_id,  # type: ignore[arg-type]
        permission="deliveries.review",
        project_id=project_id,
        site_id=site_id,
        on=utcnow().date(),
    )
    submitter = payload.get("submitted_by_id")
    if submitter:
        reviewers.discard(UUID(submitter))
    number = payload.get("delivery_number", "A delivery")
    again = event.event_type == "delivery.corrected"
    critical = payload.get("worst_severity") == "CRITICAL"
    for user_id in sorted(reviewers, key=str):
        await notification_service.send(
            session,
            company_id=event.company_id,  # type: ignore[arg-type]
            user_id=user_id,
            notification_type=NotificationType.DELIVERY_REQUIRES_REVIEW.value,
            title=f"{number} {'corrected, needs review again' if again else 'needs review'}"
            + (f" at {payload['site_code']}" if payload.get("site_code") else ""),
            body=str(payload.get("top_flag") or "It has been flagged for review."),
            priority=NotificationPriority.HIGH if critical else NotificationPriority.NORMAL,
            entity_type="Delivery",
            entity_id=event.aggregate_id,
            link_path=payload.get("link_path"),
        )


_DECISION_COPY = {
    "approved": (NotificationType.DELIVERY_APPROVED, "approved", NotificationPriority.NORMAL),
    "rejected": (NotificationType.DELIVERY_REJECTED, "rejected", NotificationPriority.HIGH),
    "correction_requested": (
        NotificationType.DELIVERY_CORRECTION_REQUESTED,
        "sent back for correction",
        NotificationPriority.HIGH,
    ),
}


@handler("delivery.decided")
async def _notify_capturer(session: AsyncSession, event: OutboxEvent) -> None:
    """Tell the person who captured a delivery what head office decided, and why."""
    payload = event.payload
    submitter = payload.get("submitted_by_id")
    copy = _DECISION_COPY.get(str(payload.get("decision")))
    if not submitter or copy is None:
        return
    if payload.get("decided_by_id") == submitter:
        return  # they decided it themselves; no need to tell them
    kind, verb, priority = copy
    number = payload.get("delivery_number", "Your delivery")
    comments = payload.get("comments")
    await notification_service.send(
        session,
        company_id=event.company_id,  # type: ignore[arg-type]
        user_id=UUID(submitter),
        notification_type=kind.value,
        title=f"{number} {verb}",
        body=f"{number} was {verb}." + (f" {comments}" if comments else ""),
        priority=priority,
        entity_type="Delivery",
        entity_id=event.aggregate_id,
        link_path=payload.get("link_path"),
    )


@handler("inventory.integrity_alarm")
async def _notify_inventory_alarm(session: AsyncSession, event: OutboxEvent) -> None:
    """Stock balances disagree with the ledger: tell the super administrators,
    who can act on it (it is a data fix, not a business decision)."""
    admins = await approver_lookup.users_holding_role(
        session,
        company_id=event.company_id,  # type: ignore[arg-type]
        role_code="SUPER_ADMIN",
        project_id=None,
        site_id=None,
        department_id=None,
        on=utcnow().date(),
    )
    findings = event.payload.get("findings") or []
    for user_id in sorted(admins, key=str):
        await notification_service.send(
            session,
            company_id=event.company_id,  # type: ignore[arg-type]
            user_id=user_id,
            notification_type=NotificationType.INVENTORY_INTEGRITY_ALARM.value,
            title=f"Stock check found {event.payload.get('count')} balance(s) that do not add up",
            body=findings[0]["detail"] if findings else "",
            priority=NotificationPriority.URGENT,
            entity_type="Inventory",
            entity_id=event.aggregate_id,
            link_path="/inventory",
        )


async def _notify_initiator(
    session: AsyncSession, event: OutboxEvent, kind: NotificationType, verb: str
) -> None:
    initiator = event.payload.get("initiated_by")
    if not initiator:
        return
    reason = event.payload.get("reason") or event.payload.get("message")
    label = _doc_label(event)
    await _notify_users(
        session,
        event,
        [initiator],
        kind=kind,
        title=f"{label[0].upper()}{label[1:]} {verb}",
        body=f"{label} was {verb}." + (f" {reason}" if reason else ""),
        priority=NotificationPriority.NORMAL
        if kind is NotificationType.APPROVAL_APPROVED
        else NotificationPriority.HIGH,
    )


@handler("approval.approved")
async def _notify_approved(session: AsyncSession, event: OutboxEvent) -> None:
    await _notify_initiator(session, event, NotificationType.APPROVAL_APPROVED, "approved")


@handler("approval.rejected")
async def _notify_rejected(session: AsyncSession, event: OutboxEvent) -> None:
    await _notify_initiator(session, event, NotificationType.APPROVAL_REJECTED, "rejected")


@handler("approval.changes_requested")
async def _notify_changes(session: AsyncSession, event: OutboxEvent) -> None:
    await _notify_initiator(
        session, event, NotificationType.APPROVAL_CHANGES_REQUESTED, "returned for changes"
    )


@handler("approval.recalled")
async def _notify_recalled(session: AsyncSession, event: OutboxEvent) -> None:
    # A recall the submitter made themselves needs no notification; an
    # automatic one (the document changed while pending) does.
    if event.payload.get("auto"):
        await _notify_initiator(session, event, NotificationType.APPROVAL_RECALLED, "recalled")


@handler("approval.stuck")
async def _notify_stuck(session: AsyncSession, event: OutboxEvent) -> None:
    await _notify_initiator(session, event, NotificationType.APPROVAL_STUCK, "stuck in approval")


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
