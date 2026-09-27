"""Head-office review of deliveries (docs/02 §6, docs/07 `/deliveries`).

A flagged delivery waits in a queue for a person. They may accept it (which
resolves its flags and approves it), reject it, send it back for correction,
waive a single flag, or attach a purchase order after the fact. Every
decision is written to `delivery_reviews`, which the database keeps append-only.

Who may do what is a permission each: reviewing is not approving, and the
person who can approve cannot necessarily reject.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope, scope_filter
from app.core.types import utcnow
from app.modules.deliveries.domain.enums import (
    DeliveryStatus,
    FlagSeverity,
    FlagStatus,
    FlagType,
    ReviewAction,
)
from app.modules.deliveries.models import Delivery, DeliveryFlag, DeliveryReview
from app.modules.deliveries.services import ingest
from app.modules.identity.services import user_lookup
from app.modules.procurement.services import po_lookup
from app.platform import outbox

PERM_REVIEW = "deliveries.review"
PERM_APPROVE = "deliveries.approve"
PERM_REJECT = "deliveries.reject"
PERM_CORRECTION = "deliveries.request_correction"
PERM_REOPEN = "deliveries.reopen"
PERM_WAIVE = "deliveries.waive_flag"

_DECIDABLE = (DeliveryStatus.SUBMITTED.value, DeliveryStatus.UNDER_REVIEW.value)


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "required", "message": message}]
    )


def _human(status: str) -> str:
    return status.lower().replace("_", " ")


async def _load(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID, permission: str
) -> Delivery:
    """Fetch and lock a delivery the caller may act on with `permission`."""
    row = (
        await session.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id, Delivery.company_id == ctx.company_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Delivery", delivery_id)
    assert_in_scope(
        ctx,
        permission,
        company_id=row.company_id,
        project_id=row.project_id,
        site_id=row.site_id,
        entity="Delivery",
    )
    await session.refresh(row, attribute_names=["items", "flags"])
    return row


async def _record(
    session: AsyncSession,
    ctx: AccessContext,
    delivery: Delivery,
    action: ReviewAction,
    *,
    comments: str | None,
    previous_status: str,
    corrected_fields: dict[str, Any] | None = None,
) -> DeliveryReview:
    people = await user_lookup.people(session, company_id=ctx.company_id, user_ids={ctx.user_id})
    review = DeliveryReview(
        delivery_id=delivery.id,
        company_id=delivery.company_id,
        action=action.value,
        reviewer_id=ctx.user_id,
        reviewer_name=people[ctx.user_id].full_name if ctx.user_id in people else None,
        reviewed_at=utcnow(),
        comments=comments,
        corrected_fields=corrected_fields,
        previous_status=previous_status,
        new_status=delivery.status,
        created_by_id=ctx.user_id,
    )
    session.add(review)
    delivery.reviewed_by_id = ctx.user_id
    delivery.reviewed_at = review.reviewed_at
    delivery.version += 1
    await session.flush()
    await _announce(session, ctx, delivery, action, comments)
    return review


# What the person who captured a delivery is told when it is decided.
_DECISIONS = {
    ReviewAction.ACCEPT: "approved",
    ReviewAction.REJECT: "rejected",
    ReviewAction.REQUEST_CORRECTION: "correction_requested",
}


async def _announce(
    session: AsyncSession,
    ctx: AccessContext,
    delivery: Delivery,
    action: ReviewAction,
    comments: str | None,
) -> None:
    """Queue the notification for a decision or a correction (delivered after
    commit by the outbox, so nobody is told about a change that rolled back)."""
    if action in _DECISIONS:
        event_type = "delivery.decided"
        extra: dict[str, Any] = {
            "decision": _DECISIONS[action],
            "comments": comments,
            "decided_by_id": str(ctx.user_id),
        }
    elif action is ReviewAction.CORRECTION_SUBMITTED:
        event_type = "delivery.corrected"
        extra = {
            "needs_review": delivery.status == DeliveryStatus.UNDER_REVIEW.value,
            "top_flag": next(
                (
                    f.message
                    for f in delivery.flags
                    if f.status == FlagStatus.OPEN.value and FlagSeverity(f.severity).needs_review
                ),
                None,
            ),
            "worst_severity": next(
                (
                    f.severity
                    for f in sorted(delivery.flags, key=lambda f: f.severity != "CRITICAL")
                    if f.status == FlagStatus.OPEN.value and FlagSeverity(f.severity).needs_review
                ),
                None,
            ),
        }
    else:
        return
    project_id = delivery.project_id
    await outbox.emit(
        session,
        outbox.DomainEvent(
            event_type=event_type,
            aggregate_type="Delivery",
            aggregate_id=delivery.id,
            payload={
                "delivery_number": delivery.delivery_number,
                "site_id": str(delivery.site_id),
                "project_id": str(project_id) if project_id else None,
                "submitted_by_id": str(delivery.submitted_by_id),
                "link_path": f"/deliveries/{delivery.id}",
                **extra,
            },
            company_id=delivery.company_id,
        ),
    )


def _open_flags(delivery: Delivery) -> list[DeliveryFlag]:
    return [
        f
        for f in delivery.flags
        if f.status == FlagStatus.OPEN.value and FlagSeverity(f.severity).needs_review
    ]


def _refresh_flag_state(delivery: Delivery) -> None:
    delivery.has_open_flags = bool(_open_flags(delivery))


# -----------------------------------------------------------------------------
# Decisions
# -----------------------------------------------------------------------------


async def accept(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID, comments: str | None
) -> Delivery:
    delivery = await _load(session, ctx, delivery_id, PERM_APPROVE)
    if delivery.status not in _DECIDABLE:
        raise BusinessRuleError(
            "delivery_not_reviewable",
            f"{delivery.delivery_number} is {_human(delivery.status)} and cannot be approved.",
        )
    open_flags = _open_flags(delivery)
    critical = [f for f in open_flags if f.severity == FlagSeverity.CRITICAL.value]
    note = (comments or "").strip()
    if critical and len(note) < 5:
        raise _fail(
            "comments",
            "Say why this is acceptable: the delivery has a critical flag "
            f"({critical[0].message}).",
        )
    previous = delivery.status
    now = utcnow()
    for flag in open_flags:
        flag.status = FlagStatus.ACCEPTED.value
        flag.resolved_by_id = ctx.user_id
        flag.resolved_at = now
        flag.resolution_note = note or None
    delivery.status = DeliveryStatus.APPROVED.value
    delivery.approved_by_id = ctx.user_id
    delivery.approved_at = now
    _refresh_flag_state(delivery)
    await _record(
        session, ctx, delivery, ReviewAction.ACCEPT, comments=note or None, previous_status=previous
    )
    return delivery


async def reject(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID, reason: str
) -> Delivery:
    delivery = await _load(session, ctx, delivery_id, PERM_REJECT)
    if delivery.status not in (*_DECIDABLE, DeliveryStatus.CORRECTION_REQUESTED.value):
        raise BusinessRuleError(
            "delivery_not_reviewable",
            f"{delivery.delivery_number} is {_human(delivery.status)} and cannot be rejected.",
        )
    if len(reason.strip()) < 5:
        raise _fail("comments", "Give the reason for rejecting this delivery.")
    previous = delivery.status
    now = utcnow()
    for flag in _open_flags(delivery):
        flag.status = FlagStatus.REJECTED.value
        flag.resolved_by_id = ctx.user_id
        flag.resolved_at = now
    delivery.status = DeliveryStatus.REJECTED.value
    delivery.rejected_by_id = ctx.user_id
    delivery.rejected_at = now
    delivery.rejection_reason = reason.strip()
    _refresh_flag_state(delivery)
    await _record(
        session,
        ctx,
        delivery,
        ReviewAction.REJECT,
        comments=reason.strip(),
        previous_status=previous,
    )
    return delivery


async def request_correction(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID, comments: str
) -> Delivery:
    delivery = await _load(session, ctx, delivery_id, PERM_CORRECTION)
    if delivery.status not in _DECIDABLE:
        raise BusinessRuleError(
            "delivery_not_reviewable",
            f"{delivery.delivery_number} is {_human(delivery.status)}; only a delivery under "
            "review can be sent back.",
        )
    if len(comments.strip()) < 5:
        raise _fail("comments", "Tell the person who captured it what to correct.")
    previous = delivery.status
    delivery.status = DeliveryStatus.CORRECTION_REQUESTED.value
    await _record(
        session,
        ctx,
        delivery,
        ReviewAction.REQUEST_CORRECTION,
        comments=comments.strip(),
        previous_status=previous,
    )
    return delivery


async def reopen(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID, reason: str
) -> Delivery:
    delivery = await _load(session, ctx, delivery_id, PERM_REOPEN)
    if delivery.status not in (DeliveryStatus.REJECTED.value, DeliveryStatus.APPROVED.value):
        raise BusinessRuleError(
            "delivery_not_reopenable",
            f"{delivery.delivery_number} is {_human(delivery.status)}; only a rejected or "
            "approved delivery can be reopened.",
        )
    if delivery.grn_id is not None:
        raise BusinessRuleError(
            "delivery_has_grn",
            "A goods received note was raised from this delivery; cancel the note first.",
        )
    if len(reason.strip()) < 5:
        raise _fail("comments", "Say why this delivery is being reopened.")
    previous = delivery.status
    delivery.status = DeliveryStatus.UNDER_REVIEW.value
    delivery.approved_by_id = delivery.approved_at = None
    delivery.rejected_by_id = delivery.rejected_at = delivery.rejection_reason = None
    await _record(
        session,
        ctx,
        delivery,
        ReviewAction.REOPEN,
        comments=reason.strip(),
        previous_status=previous,
    )
    return delivery


async def waive_flag(
    session: AsyncSession, ctx: AccessContext, flag_id: UUID, note: str
) -> Delivery:
    """Set one flag aside with a reason. When it was the last open flag on a
    delivery under review, the delivery is no longer held for it."""
    flag = (
        await session.execute(select(DeliveryFlag).where(DeliveryFlag.id == flag_id))
    ).scalar_one_or_none()
    if flag is None:
        raise NotFoundError("Delivery flag", flag_id)
    delivery = await _load(session, ctx, flag.delivery_id, PERM_WAIVE)
    flag = next(f for f in delivery.flags if f.id == flag_id)
    if flag.status != FlagStatus.OPEN.value:
        raise BusinessRuleError(
            "flag_not_open", f"That flag is already {flag.status.lower()}; nothing to waive."
        )
    if len(note.strip()) < 5:
        raise _fail("note", "Say why this flag is being waived.")
    flag.status = FlagStatus.WAIVED.value
    flag.resolved_by_id = ctx.user_id
    flag.resolved_at = utcnow()
    flag.resolution_note = note.strip()
    _refresh_flag_state(delivery)
    if delivery.status == DeliveryStatus.UNDER_REVIEW.value and not delivery.has_open_flags:
        delivery.status = DeliveryStatus.SUBMITTED.value
    delivery.version += 1
    await session.flush()
    return delivery


async def attach_purchase_order(
    session: AsyncSession,
    ctx: AccessContext,
    delivery_id: UUID,
    purchase_order_id: UUID,
    comments: str | None,
) -> Delivery:
    """Attach an order to a delivery that arrived without one (docs/12 Q3).

    The order's balance is checked as if the delivery had been captured against
    it, so attaching cannot be a way to hide an over-delivery.
    """
    delivery = await _load(session, ctx, delivery_id, PERM_REVIEW)
    if (
        DeliveryStatus(delivery.status).is_terminal
        or delivery.status == DeliveryStatus.APPROVED.value
    ):
        raise BusinessRuleError(
            "delivery_not_reviewable",
            f"{delivery.delivery_number} is {_human(delivery.status)}; reopen it to attach "
            "an order.",
        )
    if delivery.purchase_order_id is not None:
        raise BusinessRuleError("delivery_has_order", "This delivery already has an order.")
    order = await po_lookup.order(
        session, company_id=ctx.company_id, purchase_order_id=purchase_order_id
    )
    if order is None:
        raise NotFoundError("Purchase order", purchase_order_id)
    if order.vendor_id != delivery.vendor_id:
        raise BusinessRuleError(
            "purchase_order_vendor", f"{order.po_number} was placed with a different vendor."
        )
    if not order.receivable:
        raise BusinessRuleError(
            "purchase_order_not_receivable",
            f"{order.po_number} is {_human(order.status)} and cannot be received against.",
        )
    drafts = await ingest.evaluate_attachment(session, ctx, delivery, order)

    previous = delivery.status
    now = utcnow()
    delivery.purchase_order_id = order.id
    for item in delivery.items:
        matched = next((i for i in order.items if i.material_id == item.material_id), None)
        item.po_item_id = matched.id if matched else None
    for flag in delivery.flags:
        if flag.flag_type == FlagType.NO_PO.value and flag.status == FlagStatus.OPEN.value:
            flag.status = FlagStatus.CORRECTED.value
            flag.resolved_by_id = ctx.user_id
            flag.resolved_at = now
            flag.resolution_note = f"{order.po_number} attached"
    delivery.flags.extend(ingest.flag_rows(ctx, drafts))
    delivery.flag_count = len([f for f in delivery.flags if f.status != FlagStatus.CORRECTED.value])
    _refresh_flag_state(delivery)
    if delivery.status == DeliveryStatus.UNDER_REVIEW.value and not delivery.has_open_flags:
        delivery.status = DeliveryStatus.SUBMITTED.value
    elif delivery.has_open_flags and delivery.status == DeliveryStatus.SUBMITTED.value:
        delivery.status = DeliveryStatus.UNDER_REVIEW.value
    await _record(
        session,
        ctx,
        delivery,
        ReviewAction.ATTACH_PO,
        comments=(comments or "").strip() or f"{order.po_number} attached",
        previous_status=previous,
        corrected_fields={"purchase_order_id": str(order.id)},
    )
    return delivery


# -----------------------------------------------------------------------------
# The queue and the history
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueueSummary:
    total: int
    critical: int
    warning: int


async def review_queue(
    session: AsyncSession, ctx: AccessContext, *, page: PageParams
) -> tuple[list[Delivery], int]:
    """Deliveries waiting for a decision: most severe first, oldest first
    within a severity — the one that has waited longest and matters most is on top."""
    worst = (
        select(
            func.max(
                case(
                    (DeliveryFlag.severity == FlagSeverity.CRITICAL.value, 2),
                    (DeliveryFlag.severity == FlagSeverity.WARNING.value, 1),
                    else_=0,
                )
            )
        )
        .where(
            DeliveryFlag.delivery_id == Delivery.id,
            DeliveryFlag.status == FlagStatus.OPEN.value,
        )
        .correlate(Delivery)
        .scalar_subquery()
    )
    stmt = scope_filter(select(Delivery), Delivery, ctx, PERM_REVIEW).where(
        Delivery.status == DeliveryStatus.UNDER_REVIEW.value
    )
    total = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ) or 0
    rows = (
        (
            await session.execute(
                stmt.order_by(worst.desc().nulls_last(), Delivery.captured_at.asc())
                .limit(page.limit)
                .offset(page.offset)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return list(rows), total


async def record_correction(
    session: AsyncSession, ctx: AccessContext, result: ingest.CorrectionResult
) -> None:
    delivery = result.delivery
    await _record(
        session,
        ctx,
        delivery,
        ReviewAction.CORRECTION_SUBMITTED,
        comments="Corrected entry submitted",
        previous_status=result.previous_status,
    )


async def reviews_for(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID
) -> list[DeliveryReview]:
    rows = await session.execute(
        select(DeliveryReview)
        .where(
            DeliveryReview.delivery_id == delivery_id, DeliveryReview.company_id == ctx.company_id
        )
        .order_by(DeliveryReview.reviewed_at.desc())
    )
    return list(rows.scalars())


def aged(delivery: Delivery, now: datetime | None = None) -> int:
    """Hours the delivery has been waiting since it reached the server."""
    return int(((now or utcnow()) - delivery.received_at).total_seconds() // 3600)
