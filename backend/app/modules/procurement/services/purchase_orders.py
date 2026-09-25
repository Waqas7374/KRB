"""Purchase orders: raise (directly or from a selected quotation), approve
through the engine, send, amend, close or cancel.

A purchase order is the commitment. Approval is delegated to the approval
engine exactly as for purchase requests; this module supplies the routing
context, the content hash, and the effect of each outcome — chiefly the
sourcing loop that writes `sourced_quantity` back to the purchase request
lines the order fulfils.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, ValidationError, VersionConflictError
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope
from app.core.types import utcnow
from app.modules.approvals.services import engine as approvals
from app.modules.approvals.services import registry
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.org.services import company_service, document_lookup
from app.modules.procurement.domain import pricing
from app.modules.procurement.domain.enums import (
    PurchaseOrderStatus,
    PurchaseRequestStatus,
    QuotationStatus,
    RfqStatus,
)
from app.modules.procurement.models import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequest,
    PurchaseRequestItem,
    Rfq,
    VendorQuotation,
)
from app.modules.procurement.services import line_checks, quotations
from app.modules.vendors.services import vendor_lookup
from app.platform.numbering import DocumentType, next_number

DOC_TYPE = DocumentType.PURCHASE_ORDER
PERM_VIEW = "procurement.po.view"
PERM_VIEW_PRICING = "procurement.po.view_pricing"
PERM_CREATE = "procurement.po.create"
PERM_APPROVE = "procurement.po.approve"
PERM_SEND = "procurement.po.send"
PERM_AMEND = "procurement.po.amend"
PERM_CANCEL = "procurement.po.cancel"

_CLOSED_PROJECT_STATUSES = frozenset({"COMPLETED", "CLOSED", "CANCELLED"})
_ORDERED_BY = "Ordered:"  # marks an RFQ closed by an order, which a cancellation may reopen


def repository(session: AsyncSession) -> ScopedRepository[PurchaseOrder]:
    return ScopedRepository(
        session,
        PurchaseOrder,
        entity_name="Purchase order",
        sortable={
            "po_number",
            "status",
            "po_date",
            "expected_delivery_date",
            "total_amount",
            "created_at",
            "updated_at",
        },
        searchable=("po_number",),
        default_sort="-created_at",
    )


@dataclass(frozen=True, slots=True)
class PoLineInput:
    material_id: UUID
    quantity: Decimal
    unit_id: UUID
    rate: Decimal
    discount_pct: Decimal = Decimal(0)
    tax_pct: Decimal = Decimal(0)
    description: str | None = None
    pr_item_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PoInput:
    vendor_id: UUID
    project_id: UUID
    site_id: UUID | None
    phase_id: UUID | None
    cost_center_id: UUID | None
    expected_delivery_date: date | None
    delivery_address: str | None
    payment_terms: str | None
    terms_and_conditions: str | None
    items: list[PoLineInput]


def _check_version(po: PurchaseOrder, expected: int | None) -> None:
    if expected is not None and po.version != expected:
        raise VersionConflictError(
            f"{po.po_number} was changed by someone else (version {po.version}, "
            f"you had {expected}). Reload and try again."
        )


def _label(po: PurchaseOrder) -> str:
    return po.po_number + (f" rev {po.revision}" if po.revision else "")


# -----------------------------------------------------------------------------
# Validation and building
# -----------------------------------------------------------------------------


async def _validate_place(session: AsyncSession, ctx: AccessContext, data: PoInput) -> None:
    try:
        place = await document_lookup.resolve_place(
            session,
            company_id=ctx.company_id,
            project_id=data.project_id,
            site_id=data.site_id,
            phase_id=data.phase_id,
            cost_center_id=data.cost_center_id,
        )
    except document_lookup.PlaceMismatchError as exc:
        raise ValidationError(
            str(exc), errors=[{"field": exc.field, "code": "invalid", "message": str(exc)}]
        ) from exc
    if place.project_status in _CLOSED_PROJECT_STATUSES:
        raise BusinessRuleError(
            "project_not_open",
            f"Project {place.project_code} is {place.project_status.lower()}; "
            "no purchase order can be raised against it.",
        )
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=ctx.company_id,
        project_id=data.project_id,
        site_id=data.site_id,
        entity="Project",
    )


async def _check_vendor(session: AsyncSession, company_id: UUID, vendor_id: UUID) -> None:
    info = (
        await vendor_lookup.vendors(session, company_id=company_id, vendor_ids={vendor_id})
    ).get(vendor_id)
    if info is None:
        raise ValidationError(
            "Unknown vendor.",
            errors=[{"field": "vendor_id", "code": "invalid", "message": "unknown vendor"}],
        )
    if not info.can_receive_orders:
        raise BusinessRuleError(
            "vendor_not_active",
            f"{info.name} is {info.status.lower()} and cannot receive purchase orders.",
        )


async def _request_items(
    session: AsyncSession, company_id: UUID, pr_item_ids: set[UUID]
) -> dict[UUID, tuple[PurchaseRequestItem, PurchaseRequest]]:
    if not pr_item_ids:
        return {}
    rows = (
        await session.execute(
            select(PurchaseRequestItem, PurchaseRequest)
            .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestItem.request_id)
            .where(
                PurchaseRequest.company_id == company_id,
                PurchaseRequestItem.id.in_(list(pr_item_ids)),
            )
        )
    ).tuples()
    return {item.id: (item, request) for item, request in rows.all()}


async def _check_sourcing(
    session: AsyncSession,
    company_id: UUID,
    project_id: UUID,
    lines: list[PoLineInput],
) -> None:
    """Each line linked to a request line must fit in what is still unsourced,
    and the request must be approved and belong to the same project."""
    linked = {line.pr_item_id for line in lines if line.pr_item_id is not None}
    found = await _request_items(session, company_id, {i for i in linked if i is not None})
    errors: list[dict[str, str]] = []
    wanted: dict[UUID, Decimal] = {}
    for index, line in enumerate(lines):
        if line.pr_item_id is None:
            continue
        path = f"items.{index}"
        entry = found.get(line.pr_item_id)
        if entry is None:
            errors.append(
                {
                    "field": f"{path}.pr_item_id",
                    "code": "invalid",
                    "message": "not a purchase request line",
                }
            )
            continue
        item, request = entry
        if request.project_id != project_id:
            errors.append(
                {
                    "field": f"{path}.pr_item_id",
                    "code": "invalid",
                    "message": f"belongs to {request.pr_number}, a different project",
                }
            )
            continue
        if request.status not in {
            PurchaseRequestStatus.APPROVED.value,
            PurchaseRequestStatus.PARTIALLY_SOURCED.value,
        }:
            errors.append(
                {
                    "field": f"{path}.pr_item_id",
                    "code": "invalid",
                    "message": f"{request.pr_number} is {request.status.lower()}, not approved",
                }
            )
            continue
        if line.material_id != item.material_id:
            errors.append(
                {
                    "field": f"{path}.material_id",
                    "code": "invalid",
                    "message": f"{request.pr_number} line {item.line_no} is for another material",
                }
            )
            continue
        wanted[line.pr_item_id] = wanted.get(line.pr_item_id, Decimal(0)) + line.quantity
        free = item.quantity - item.sourced_quantity
        if wanted[line.pr_item_id] > free:
            errors.append(
                {
                    "field": f"{path}.quantity",
                    "code": "invalid",
                    "message": f"only {free:f} of {request.pr_number} line {item.line_no} "
                    "is still unsourced",
                }
            )
    if errors:
        raise ValidationError("Some lines cannot be sourced.", errors=errors)


def _build_items(ctx: AccessContext, lines: list[PoLineInput]) -> list[PurchaseOrderItem]:
    items = []
    for index, line in enumerate(lines):
        amount = pricing.price_line(line.quantity, line.rate, line.discount_pct, line.tax_pct)
        items.append(
            PurchaseOrderItem(
                line_no=index + 1,
                material_id=line.material_id,
                description=line.description,
                quantity=line.quantity,
                unit_id=line.unit_id,
                rate=line.rate,
                discount_pct=line.discount_pct,
                tax_pct=line.tax_pct,
                tax_amount=amount.tax,
                line_total=amount.total,
                received_quantity=Decimal(0),
                accepted_quantity=Decimal(0),
                invoiced_quantity=Decimal(0),
                pr_item_id=line.pr_item_id,
                created_by_id=ctx.user_id,
            )
        )
    return items


def _set_totals(po: PurchaseOrder, lines: list[PoLineInput]) -> None:
    totals = pricing.total_lines(
        pricing.price_line(i.quantity, i.rate, i.discount_pct, i.tax_pct) for i in lines
    )
    po.subtotal = totals.subtotal
    po.discount_amount = totals.discount_amount
    po.tax_amount = totals.tax_amount
    po.total_amount = totals.total_amount


async def _next_number(session: AsyncSession, ctx: AccessContext) -> str:
    return await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DOC_TYPE,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------


async def create(
    session: AsyncSession,
    ctx: AccessContext,
    data: PoInput,
    *,
    quotation: VendorQuotation | None = None,
) -> PurchaseOrder:
    if not data.items:
        raise ValidationError(
            "A purchase order needs at least one line.",
            errors=[{"field": "items", "code": "required", "message": "add at least one line"}],
        )
    await _validate_place(session, ctx, data)
    await _check_vendor(session, ctx.company_id, data.vendor_id)
    await line_checks.check_materials_and_units(
        session,
        company_id=ctx.company_id,
        lines=[(i.material_id, i.unit_id) for i in data.items],
    )
    await _check_sourcing(session, ctx.company_id, data.project_id, data.items)

    po = PurchaseOrder(
        company_id=ctx.company_id,
        po_number=await _next_number(session, ctx),
        status=PurchaseOrderStatus.DRAFT.value,
        revision=0,
        vendor_id=data.vendor_id,
        project_id=data.project_id,
        site_id=data.site_id,
        phase_id=data.phase_id,
        cost_center_id=data.cost_center_id,
        quotation_id=quotation.id if quotation is not None else None,
        po_date=utcnow().date(),
        expected_delivery_date=data.expected_delivery_date,
        delivery_address=data.delivery_address,
        payment_terms=data.payment_terms,
        terms_and_conditions=data.terms_and_conditions,
        currency_code=quotation.currency_code if quotation is not None else "PKR",
        created_by_id=ctx.user_id,
        items=_build_items(ctx, data.items),
    )
    _set_totals(po, data.items)
    session.add(po)
    await session.flush()
    return po


async def create_from_quotation(
    session: AsyncSession, ctx: AccessContext, quotation_id: UUID
) -> PurchaseOrder:
    quotation = await quotations.get(session, ctx, quotation_id)
    if quotation.status != QuotationStatus.SELECTED.value:
        raise BusinessRuleError(
            "quotation_not_selected",
            f"{quotation.quotation_number} has not been selected. Choose a winning quotation "
            "(with the reason) before ordering from it.",
        )
    if await quotations.live_order_for(session, quotation.id) is not None:
        raise BusinessRuleError(
            "quotation_ordered",
            f"A purchase order already exists for {quotation.quotation_number}.",
        )
    rfq = await session.get(Rfq, quotation.rfq_id)
    assert rfq is not None
    await session.refresh(rfq, attribute_names=["items"])
    rfq_items = {i.id: i for i in rfq.items}
    lines = [
        PoLineInput(
            material_id=item.material_id,
            quantity=item.quantity,
            unit_id=item.unit_id,
            rate=item.rate,
            discount_pct=item.discount_pct,
            tax_pct=item.tax_pct,
            description=rfq_items[item.rfq_item_id].description,
            pr_item_id=rfq_items[item.rfq_item_id].pr_item_id,
        )
        for item in quotation.items
    ]
    days = max((i.delivery_days or 0 for i in quotation.items), default=0)
    days = max(days, quotation.delivery_days or 0)
    data = PoInput(
        vendor_id=quotation.vendor_id,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        phase_id=None,
        cost_center_id=None,
        expected_delivery_date=utcnow().date() + timedelta(days=days) if days else None,
        delivery_address=None,
        payment_terms=quotation.payment_terms,
        terms_and_conditions=rfq.terms,
        items=lines,
    )
    return await create(session, ctx, data, quotation=quotation)


async def _get_for_update(session: AsyncSession, ctx: AccessContext, po_id: UUID) -> PurchaseOrder:
    po = await repository(session).get_for_update(ctx, PERM_VIEW, po_id)
    await session.refresh(po, attribute_names=["items"])
    return po


def _assert_scope(ctx: AccessContext, permission: str, po: PurchaseOrder) -> None:
    assert_in_scope(
        ctx,
        permission,
        company_id=po.company_id,
        project_id=po.project_id,
        site_id=po.site_id,
        entity="Purchase order",
    )


def _humanise(status: str) -> str:
    return status.lower().replace("_", " ")


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    po_id: UUID,
    data: PoInput,
    *,
    expected_version: int | None,
) -> PurchaseOrder:
    po = await _get_for_update(session, ctx, po_id)
    _check_version(po, expected_version)
    _assert_scope(ctx, PERM_CREATE, po)
    if not PurchaseOrderStatus(po.status).is_editable:
        raise BusinessRuleError(
            "purchase_order_locked",
            f"{po.po_number} is {_humanise(po.status)} and can no longer be edited."
            + (
                " Recall it from approval first."
                if po.status == PurchaseOrderStatus.PENDING_APPROVAL.value
                else " Use amend to reopen an approved order."
            ),
        )
    if not data.items:
        raise ValidationError(
            "A purchase order needs at least one line.",
            errors=[{"field": "items", "code": "required", "message": "add at least one line"}],
        )
    if po.quotation_id is not None and data.vendor_id != po.vendor_id:
        raise ValidationError(
            "The vendor of an order raised from a quotation cannot be changed.",
            errors=[{"field": "vendor_id", "code": "invalid", "message": "fixed by the quotation"}],
        )
    await _validate_place(session, ctx, data)
    await _check_vendor(session, ctx.company_id, data.vendor_id)
    await line_checks.check_materials_and_units(
        session,
        company_id=ctx.company_id,
        lines=[(i.material_id, i.unit_id) for i in data.items],
    )
    await _check_sourcing(session, ctx.company_id, data.project_id, data.items)

    po.vendor_id = data.vendor_id
    po.project_id = data.project_id
    po.site_id = data.site_id
    po.phase_id = data.phase_id
    po.cost_center_id = data.cost_center_id
    po.expected_delivery_date = data.expected_delivery_date
    po.delivery_address = data.delivery_address
    po.payment_terms = data.payment_terms
    po.terms_and_conditions = data.terms_and_conditions
    po.items.clear()
    await session.flush()
    po.items.extend(_build_items(ctx, data.items))
    _set_totals(po, data.items)
    po.updated_by_id = ctx.user_id
    po.version += 1
    await session.flush()
    return po


async def submit(
    session: AsyncSession, ctx: AccessContext, po_id: UUID, *, expected_version: int | None
) -> PurchaseOrder:
    po = await _get_for_update(session, ctx, po_id)
    _check_version(po, expected_version)
    _assert_scope(ctx, PERM_CREATE, po)
    if not PurchaseOrderStatus(po.status).is_editable:
        raise BusinessRuleError(
            "purchase_order_not_submittable",
            f"{po.po_number} is {_humanise(po.status)}; only a draft, rejected or returned "
            "order can be submitted.",
        )
    if not po.items:
        raise BusinessRuleError("purchase_order_empty", "The order has no lines.")
    await _check_vendor(session, po.company_id, po.vendor_id)
    await _check_sourcing(
        session,
        po.company_id,
        po.project_id,
        [
            PoLineInput(
                material_id=i.material_id,
                quantity=i.quantity,
                unit_id=i.unit_id,
                rate=i.rate,
                pr_item_id=i.pr_item_id,
            )
            for i in po.items
        ],
    )

    place = await document_lookup.resolve_place(
        session,
        company_id=po.company_id,
        project_id=po.project_id,
        site_id=po.site_id,
        phase_id=po.phase_id,
    )
    vendor = (
        await vendor_lookup.vendors(session, company_id=po.company_id, vendor_ids={po.vendor_id})
    )[po.vendor_id]
    quotation = await session.get(VendorQuotation, po.quotation_id) if po.quotation_id else None
    subject = approvals.ApprovalSubject(
        doc_type=DOC_TYPE,
        doc_id=po.id,
        company_id=po.company_id,
        initiated_by=ctx.user_id,
        context=_approval_context(po, place, vendor, quotation, ctx),
        document_hash=_hash(po),
        doc_number=_label(po),
        summary=f"{vendor.name} — {place.project_code}"
        + (f" / {place.site_code}" if place.site_code else "")
        + (f" (amendment {po.revision}: {po.amendment_reason})" if po.revision else ""),
        amount=po.total_amount,
        currency_code=po.currency_code,
        link_path=f"/purchase-orders/{po.id}",
        project_id=po.project_id,
        site_id=po.site_id,
    )
    async with session.begin_nested():
        po.status = PurchaseOrderStatus.PENDING_APPROVAL.value
        po.submitted_at = utcnow()
        po.decision_reason = None
        po.version += 1
        await session.flush()
        approval = await approvals.submit(session, subject)
        po.approval_request_id = approval.id
        await session.flush()
    return po


async def amend(
    session: AsyncSession,
    ctx: AccessContext,
    po_id: UUID,
    reason: str,
    *,
    expected_version: int | None,
) -> PurchaseOrder:
    """Reopen an approved order for change. It goes back to draft, gives up the
    request quantities it had claimed, and must be approved again — otherwise
    an approved 400 000 order could quietly become a 4 000 000 one."""
    po = await _get_for_update(session, ctx, po_id)
    _check_version(po, expected_version)
    _assert_scope(ctx, PERM_AMEND, po)
    amendable = {
        PurchaseOrderStatus.APPROVED.value,
        PurchaseOrderStatus.SENT.value,
        PurchaseOrderStatus.ACKNOWLEDGED.value,
    }
    if po.status not in amendable:
        raise BusinessRuleError(
            "purchase_order_not_amendable",
            f"{po.po_number} is {_humanise(po.status)}; only an approved, sent or acknowledged "
            "order can be amended.",
        )
    if any(i.received_quantity > 0 for i in po.items):
        raise BusinessRuleError(
            "purchase_order_received",
            "Goods have already been received against this order. Close it and raise a new "
            "order for the remainder.",
        )
    await release_sourcing(session, po)
    was = po.status
    po.status = PurchaseOrderStatus.DRAFT.value
    po.revision += 1
    po.amendment_reason = reason.strip()
    po.approval_request_id = None
    po.approved_at = None
    po.sent_at = None
    po.acknowledged_at = None
    po.decision_reason = None
    po.version += 1
    await record_audit(
        session,
        action=AuditAction.REOPEN,
        entity_type="PurchaseOrder",
        entity_id=po.id,
        entity_label=po.po_number,
        project_id=po.project_id,
        site_id=po.site_id,
        summary=f"{po.po_number} reopened for amendment {po.revision} ({was} -> DRAFT): "
        f"{reason.strip()}",
    )
    await session.flush()
    return po


async def send(
    session: AsyncSession, ctx: AccessContext, po_id: UUID, *, expected_version: int | None
) -> PurchaseOrder:
    po = await _get_for_update(session, ctx, po_id)
    _check_version(po, expected_version)
    _assert_scope(ctx, PERM_SEND, po)
    if po.status != PurchaseOrderStatus.APPROVED.value:
        raise BusinessRuleError(
            "purchase_order_not_approved",
            f"{po.po_number} is {_humanise(po.status)}; only an approved order can be sent.",
        )
    # Approved a month ago, vendor suspended since: do not send.
    await _check_vendor(session, po.company_id, po.vendor_id)
    po.status = PurchaseOrderStatus.SENT.value
    po.sent_at = utcnow()
    po.version += 1
    await record_audit(
        session,
        action=AuditAction.UPDATE,
        entity_type="PurchaseOrder",
        entity_id=po.id,
        entity_label=po.po_number,
        project_id=po.project_id,
        site_id=po.site_id,
        summary=f"{_label(po)} sent to the vendor",
    )
    await session.flush()
    return po


async def acknowledge(
    session: AsyncSession, ctx: AccessContext, po_id: UUID, *, expected_version: int | None
) -> PurchaseOrder:
    po = await _get_for_update(session, ctx, po_id)
    _check_version(po, expected_version)
    _assert_scope(ctx, PERM_SEND, po)
    if po.status != PurchaseOrderStatus.SENT.value:
        raise BusinessRuleError(
            "purchase_order_not_sent",
            f"{po.po_number} is {_humanise(po.status)}; only a sent order can be acknowledged.",
        )
    po.status = PurchaseOrderStatus.ACKNOWLEDGED.value
    po.acknowledged_at = utcnow()
    po.version += 1
    await session.flush()
    return po


async def cancel(
    session: AsyncSession, ctx: AccessContext, po_id: UUID, reason: str
) -> PurchaseOrder:
    po = await _get_for_update(session, ctx, po_id)
    _assert_scope(ctx, PERM_CANCEL, po)
    allowed = {
        PurchaseOrderStatus.DRAFT.value,
        PurchaseOrderStatus.REJECTED.value,
        PurchaseOrderStatus.CHANGES_REQUESTED.value,
        PurchaseOrderStatus.APPROVED.value,
        PurchaseOrderStatus.SENT.value,
        PurchaseOrderStatus.ACKNOWLEDGED.value,
    }
    if po.status not in allowed:
        raise BusinessRuleError(
            "purchase_order_not_cancellable",
            f"{po.po_number} is {_humanise(po.status)} and cannot be cancelled."
            + (
                " Recall it from approval first."
                if po.status == PurchaseOrderStatus.PENDING_APPROVAL.value
                else " If goods were received, close it instead."
            ),
        )
    if any(i.received_quantity > 0 for i in po.items):
        raise BusinessRuleError(
            "purchase_order_received",
            "Goods have been received against this order; close it instead of cancelling.",
        )
    previous = po.status
    if PurchaseOrderStatus(previous).is_committed:
        await release_sourcing(session, po)
    po.status = PurchaseOrderStatus.CANCELLED.value
    po.cancelled_at = utcnow()
    po.cancel_reason = reason.strip()
    po.version += 1
    await _reopen_rfq(session, po)
    await record_audit(
        session,
        action=AuditAction.CANCEL,
        entity_type="PurchaseOrder",
        entity_id=po.id,
        entity_label=po.po_number,
        project_id=po.project_id,
        site_id=po.site_id,
        summary=f"{_label(po)} cancelled ({previous} -> CANCELLED): {reason.strip()}",
    )
    await session.flush()
    return po


async def close(
    session: AsyncSession, ctx: AccessContext, po_id: UUID, reason: str
) -> PurchaseOrder:
    """End an order with goods still owing. What was never received is given
    back to the request so it can be sourced elsewhere."""
    po = await _get_for_update(session, ctx, po_id)
    _assert_scope(ctx, PERM_CANCEL, po)
    closable = {
        PurchaseOrderStatus.SENT.value,
        PurchaseOrderStatus.ACKNOWLEDGED.value,
        PurchaseOrderStatus.PARTIALLY_RECEIVED.value,
        PurchaseOrderStatus.RECEIVED.value,
    }
    if po.status not in closable:
        raise BusinessRuleError(
            "purchase_order_not_closable",
            f"{po.po_number} is {_humanise(po.status)}; only an order that was sent can be "
            "closed. Cancel it instead.",
        )
    await release_sourcing(session, po, unreceived_only=True)
    po.status = PurchaseOrderStatus.CLOSED.value
    po.closed_at = utcnow()
    po.close_reason = reason.strip()
    po.version += 1
    await record_audit(
        session,
        action=AuditAction.CLOSE,
        entity_type="PurchaseOrder",
        entity_id=po.id,
        entity_label=po.po_number,
        project_id=po.project_id,
        site_id=po.site_id,
        summary=f"{_label(po)} closed: {reason.strip()}",
    )
    await session.flush()
    return po


async def list_orders(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[PurchaseOrder], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, po_id: UUID) -> PurchaseOrder:
    return await repository(session).get(ctx, PERM_VIEW, po_id)


# -----------------------------------------------------------------------------
# The sourcing loop: purchase order -> purchase request
# -----------------------------------------------------------------------------


async def _lock_request_items(
    session: AsyncSession, pr_item_ids: set[UUID]
) -> dict[UUID, PurchaseRequestItem]:
    rows = (
        (
            await session.execute(
                select(PurchaseRequestItem)
                .where(PurchaseRequestItem.id.in_(list(pr_item_ids)))
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return {r.id: r for r in rows}


async def _refresh_request_status(session: AsyncSession, request_ids: set[UUID]) -> None:
    for request_id in request_ids:
        request = (
            await session.execute(select(PurchaseRequest).where(PurchaseRequest.id == request_id))
        ).scalar_one()
        await session.refresh(request, attribute_names=["items"])
        if request.status not in {
            PurchaseRequestStatus.APPROVED.value,
            PurchaseRequestStatus.PARTIALLY_SOURCED.value,
            PurchaseRequestStatus.SOURCED.value,
        }:
            continue
        if all(i.sourced_quantity >= i.quantity for i in request.items):
            new = PurchaseRequestStatus.SOURCED
        elif any(i.sourced_quantity > 0 for i in request.items):
            new = PurchaseRequestStatus.PARTIALLY_SOURCED
        else:
            new = PurchaseRequestStatus.APPROVED
        if request.status != new.value:
            request.status = new.value
            request.version += 1


async def claim_sourcing(session: AsyncSession, po: PurchaseOrder) -> None:
    """An approved order takes its quantities from the request lines it links.
    Checked again here, under a row lock, because two orders approved at once
    could each have passed the check made when they were drafted."""
    quantities: dict[UUID, Decimal] = {}
    for item in po.items:
        if item.pr_item_id is not None:
            quantities[item.pr_item_id] = (
                quantities.get(item.pr_item_id, Decimal(0)) + item.quantity
            )
    if not quantities:
        return
    locked = await _lock_request_items(session, set(quantities))
    for pr_item_id, quantity in quantities.items():
        line = locked[pr_item_id]
        if line.sourced_quantity + quantity > line.quantity:
            raise BusinessRuleError(
                "purchase_request_oversourced",
                f"{_label(po)} would source {quantity:f} of a request line that has only "
                f"{line.quantity - line.sourced_quantity:f} left. Another order has taken "
                "the rest; amend this order and approve it again.",
            )
        line.sourced_quantity = line.sourced_quantity + quantity
    await session.flush()
    await _refresh_request_status(session, {line.request_id for line in locked.values()})


async def release_sourcing(
    session: AsyncSession, po: PurchaseOrder, *, unreceived_only: bool = False
) -> None:
    quantities: dict[UUID, Decimal] = {}
    for item in po.items:
        if item.pr_item_id is None:
            continue
        give_back = item.quantity - (item.received_quantity if unreceived_only else Decimal(0))
        if give_back > 0:
            quantities[item.pr_item_id] = quantities.get(item.pr_item_id, Decimal(0)) + give_back
    if not quantities:
        return
    locked = await _lock_request_items(session, set(quantities))
    for pr_item_id, quantity in quantities.items():
        line = locked[pr_item_id]
        line.sourced_quantity = max(Decimal(0), line.sourced_quantity - quantity)
    await session.flush()
    await _refresh_request_status(session, {line.request_id for line in locked.values()})


async def _close_rfq_for(session: AsyncSession, po: PurchaseOrder) -> None:
    if po.quotation_id is None:
        return
    quotation = await session.get(VendorQuotation, po.quotation_id)
    if quotation is None:
        return
    rfq = await session.get(Rfq, quotation.rfq_id)
    if rfq is not None and rfq.status == RfqStatus.ISSUED.value:
        from app.modules.procurement.services import rfqs

        rfqs.mark_closed(rfq, f"{_ORDERED_BY} {po.po_number}")


async def _reopen_rfq(session: AsyncSession, po: PurchaseOrder) -> None:
    """A cancelled order gives its RFQ back, if the order is what closed it, so
    another vendor can be chosen."""
    if po.quotation_id is None:
        return
    quotation = await session.get(VendorQuotation, po.quotation_id)
    rfq = await session.get(Rfq, quotation.rfq_id) if quotation is not None else None
    if (
        rfq is not None
        and rfq.status == RfqStatus.CLOSED.value
        and (rfq.close_reason or "").startswith(_ORDERED_BY)
    ):
        rfq.status = RfqStatus.ISSUED.value
        rfq.closed_at = None
        rfq.close_reason = None
        rfq.version += 1


# -----------------------------------------------------------------------------
# Approval integration
# -----------------------------------------------------------------------------

CONTEXT_VARIABLES = frozenset(
    {
        "total_amount",
        "currency",
        "item_count",
        "max_line_amount",
        "is_amendment",
        "has_quotation",
        "variance_vs_quotation_pct",
        "vendor.*",
        "project.*",
        "site.*",
        "phase.*",
        "requester.*",
    }
)


def _approval_context(
    po: PurchaseOrder,
    place: document_lookup.DocumentPlace,
    vendor: vendor_lookup.VendorInfo,
    quotation: VendorQuotation | None,
    ctx: AccessContext,
) -> dict[str, Any]:
    """The documented variables a PO workflow condition may use (docs/04 §1).

    `variance_vs_quotation_pct` is how far the order's total sits above (+) or
    below (-) the quotation it came from: an order that quietly grew after the
    vendor was chosen is exactly what a workflow should be able to route.
    """
    context: dict[str, Any] = {
        "total_amount": po.total_amount,
        "currency": po.currency_code,
        "item_count": len(po.items),
        "max_line_amount": max((i.line_total for i in po.items), default=Decimal(0)),
        "is_amendment": po.revision > 0,
        "has_quotation": quotation is not None,
        "vendor": {"id": vendor.id, "code": vendor.code, "name": vendor.name},
        "project": {"id": place.project_id, "code": place.project_code, "name": place.project_name},
        "site": {"id": place.site_id, "code": place.site_code},
        "phase": {"id": place.phase_id, "code": place.phase_code},
        "requester": {"id": ctx.user_id, "roles": sorted(ctx.role_codes)},
    }
    if quotation is not None and quotation.total_amount > 0:
        context["variance_vs_quotation_pct"] = (
            (po.total_amount - quotation.total_amount) / quotation.total_amount * 100
        ).quantize(Decimal("0.01"))
    return context


def _hash(po: PurchaseOrder) -> str:
    return approvals.document_hash(
        {
            "vendor_id": po.vendor_id,
            "project_id": po.project_id,
            "site_id": po.site_id,
            "phase_id": po.phase_id,
            "cost_center_id": po.cost_center_id,
            "quotation_id": po.quotation_id,
            "revision": po.revision,
            "expected_delivery_date": po.expected_delivery_date,
            "delivery_address": po.delivery_address,
            "payment_terms": po.payment_terms,
            "terms_and_conditions": po.terms_and_conditions,
            "currency": po.currency_code,
            "total_amount": po.total_amount,
            "items": [
                {
                    "material_id": i.material_id,
                    "quantity": i.quantity,
                    "unit_id": i.unit_id,
                    "rate": i.rate,
                    "discount_pct": i.discount_pct,
                    "tax_pct": i.tax_pct,
                    "pr_item_id": i.pr_item_id,
                    "description": i.description,
                }
                for i in sorted(po.items, key=lambda i: i.line_no)
            ],
        }
    )


class PurchaseOrderApprovals:
    spec = registry.DocumentTypeSpec(
        doc_type=DOC_TYPE,
        label="Purchase order",
        approve_permission=PERM_APPROVE,
        context_variables=CONTEXT_VARIABLES,
    )

    async def _load(self, session: AsyncSession, doc_id: UUID) -> PurchaseOrder:
        po = (
            await session.execute(select(PurchaseOrder).where(PurchaseOrder.id == doc_id))
        ).scalar_one()
        await session.refresh(po, attribute_names=["items"])
        return po

    async def current_hash(self, session: AsyncSession, doc_id: UUID) -> str | None:
        return _hash(await self._load(session, doc_id))

    async def _set(
        self,
        session: AsyncSession,
        outcome: registry.ApprovalOutcome,
        status: PurchaseOrderStatus,
    ) -> PurchaseOrder:
        po = await self._load(session, outcome.doc_id)
        po.status = status.value
        po.decision_reason = outcome.reason
        po.version += 1
        return po

    async def on_approved(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        po = await self._set(session, outcome, PurchaseOrderStatus.APPROVED)
        po.approved_at = utcnow()
        # Approval is where the order starts to count: it takes its quantities
        # from the request and, if it came from an RFQ, ends that RFQ.
        await claim_sourcing(session, po)
        await _close_rfq_for(session, po)

    async def on_rejected(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PurchaseOrderStatus.REJECTED)

    async def on_changes_requested(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome
    ) -> None:
        await self._set(session, outcome, PurchaseOrderStatus.CHANGES_REQUESTED)

    async def on_recalled(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._set(session, outcome, PurchaseOrderStatus.DRAFT)


registry.register(PurchaseOrderApprovals())
