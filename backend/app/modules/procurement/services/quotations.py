"""Vendor quotations: record, compare, select.

The comparison is the point. It lays every vendor's answer side by side, marks
the lowest price on each line, and stops there. Choosing is a person's act and
carries a written reason (§10) — including when the cheapest bid is the one
chosen, because the reason is what an auditor reads a year later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import (
    BusinessRuleError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope
from app.core.types import utcnow
from app.modules.audit.domain.enums import AuditAction
from app.modules.audit.services.writer import record as record_audit
from app.modules.org.services import company_service
from app.modules.procurement.domain import pricing
from app.modules.procurement.domain.enums import (
    PurchaseOrderStatus,
    QuotationStatus,
    RfqStatus,
    RfqVendorStatus,
)
from app.modules.procurement.models import (
    PurchaseOrder,
    PurchaseRequestItem,
    Rfq,
    RfqItem,
    VendorQuotation,
    VendorQuotationItem,
)
from app.modules.procurement.services import rfqs
from app.platform.numbering import DocumentType, next_number

DOC_TYPE = DocumentType.QUOTATION
PERM_VIEW = "procurement.quotation.view"
PERM_RECORD = "procurement.quotation.record"
PERM_SELECT = "procurement.quotation.select"

_OPEN = (QuotationStatus.RECEIVED.value, QuotationStatus.SHORTLISTED.value)
# An order in any of these no longer stands, so its quotation may be re-opened.
_DEAD_PO = (PurchaseOrderStatus.CANCELLED.value, PurchaseOrderStatus.REJECTED.value)


def repository(session: AsyncSession) -> ScopedRepository[VendorQuotation]:
    return ScopedRepository(
        session,
        VendorQuotation,
        entity_name="Quotation",
        sortable={
            "quotation_number",
            "status",
            "quote_date",
            "total_amount",
            "created_at",
            "updated_at",
        },
        searchable=("quotation_number", "vendor_reference"),
        default_sort="-created_at",
    )


@dataclass(frozen=True, slots=True)
class QuotationLineInput:
    rfq_item_id: UUID
    rate: Decimal
    discount_pct: Decimal = Decimal(0)
    tax_pct: Decimal = Decimal(0)
    quantity: Decimal | None = None
    delivery_days: int | None = None
    remarks: str | None = None


@dataclass(frozen=True, slots=True)
class QuotationInput:
    vendor_id: UUID
    vendor_reference: str | None
    quote_date: date
    valid_until: date | None
    delivery_days: int | None
    payment_terms: str | None
    notes: str | None
    items: list[QuotationLineInput]


def _check_version(quotation: VendorQuotation, expected: int | None) -> None:
    if expected is not None and quotation.version != expected:
        raise VersionConflictError(
            f"{quotation.quotation_number} was changed by someone else "
            f"(version {quotation.version}, you had {expected}). Reload and try again."
        )


async def _rfq_for(session: AsyncSession, ctx: AccessContext, rfq_id: UUID) -> Rfq:
    rfq = await rfqs.repository(session).get_for_update(ctx, rfqs.PERM_VIEW, rfq_id)
    await session.refresh(rfq, attribute_names=["items", "vendors"])
    return rfq


def _build_lines(
    ctx: AccessContext, rfq: Rfq, data: QuotationInput
) -> tuple[list[VendorQuotationItem], pricing.DocumentTotals]:
    if not data.items:
        raise ValidationError(
            "A quotation needs at least one priced line.",
            errors=[{"field": "items", "code": "required", "message": "quote at least one line"}],
        )
    by_id: dict[UUID, RfqItem] = {i.id: i for i in rfq.items}
    errors: list[dict[str, str]] = []
    seen: set[UUID] = set()
    lines: list[VendorQuotationItem] = []
    amounts: list[pricing.LineAmounts] = []
    for index, line in enumerate(data.items):
        path = f"items.{index}"
        wanted = by_id.get(line.rfq_item_id)
        if wanted is None:
            errors.append(
                {
                    "field": f"{path}.rfq_item_id",
                    "code": "invalid",
                    "message": "not a line of this RFQ",
                }
            )
            continue
        if line.rfq_item_id in seen:
            errors.append(
                {
                    "field": f"{path}.rfq_item_id",
                    "code": "duplicate",
                    "message": "this RFQ line is already quoted above",
                }
            )
            continue
        seen.add(line.rfq_item_id)
        quantity = line.quantity if line.quantity is not None else wanted.quantity
        if quantity <= 0 or quantity > wanted.quantity:
            errors.append(
                {
                    "field": f"{path}.quantity",
                    "code": "invalid",
                    "message": f"must be more than 0 and at most the {wanted.quantity:f} requested",
                }
            )
            continue
        amount = pricing.price_line(quantity, line.rate, line.discount_pct, line.tax_pct)
        amounts.append(amount)
        lines.append(
            VendorQuotationItem(
                line_no=len(lines) + 1,
                rfq_item_id=wanted.id,
                material_id=wanted.material_id,
                quantity=quantity,
                unit_id=wanted.unit_id,
                rate=line.rate,
                discount_pct=line.discount_pct,
                tax_pct=line.tax_pct,
                line_total=amount.total,
                delivery_days=line.delivery_days,
                remarks=line.remarks,
                created_by_id=ctx.user_id,
            )
        )
    if data.valid_until is not None and data.valid_until < data.quote_date:
        errors.append(
            {
                "field": "valid_until",
                "code": "invalid",
                "message": "cannot be before the quotation date",
            }
        )
    if errors:
        raise ValidationError("The quotation is not valid.", errors=errors)
    return lines, pricing.total_lines(amounts)


def _apply_header(
    quotation: VendorQuotation, data: QuotationInput, totals: pricing.DocumentTotals
) -> None:
    quotation.vendor_reference = data.vendor_reference
    quotation.quote_date = data.quote_date
    quotation.valid_until = data.valid_until
    quotation.delivery_days = data.delivery_days
    quotation.payment_terms = data.payment_terms
    quotation.notes = data.notes
    quotation.subtotal = totals.subtotal
    quotation.discount_amount = totals.discount_amount
    quotation.tax_amount = totals.tax_amount
    quotation.total_amount = totals.total_amount


async def record(
    session: AsyncSession, ctx: AccessContext, rfq_id: UUID, data: QuotationInput
) -> VendorQuotation:
    rfq = await _rfq_for(session, ctx, rfq_id)
    assert_in_scope(
        ctx,
        PERM_RECORD,
        company_id=rfq.company_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        entity="RFQ",
    )
    if rfq.status != RfqStatus.ISSUED.value:
        raise BusinessRuleError(
            "rfq_not_issued",
            f"{rfq.rfq_number} is {rfq.status.lower()}; quotations can be recorded only "
            "against an issued RFQ.",
        )
    invitation = next((v for v in rfq.vendors if v.vendor_id == data.vendor_id), None)
    if invitation is None:
        raise ValidationError(
            "That vendor was not invited to this RFQ.",
            errors=[{"field": "vendor_id", "code": "invalid", "message": "not invited"}],
        )
    if invitation.status == RfqVendorStatus.QUOTED.value:
        raise BusinessRuleError(
            "quotation_exists",
            "This vendor has already quoted. Edit the existing quotation instead.",
        )
    lines, totals = _build_lines(ctx, rfq, data)
    number = await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DOC_TYPE,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )
    quotation = VendorQuotation(
        company_id=ctx.company_id,
        quotation_number=number,
        rfq_id=rfq.id,
        vendor_id=data.vendor_id,
        project_id=rfq.project_id,
        site_id=rfq.site_id,
        currency_code=rfq.currency_code,
        status=QuotationStatus.RECEIVED.value,
        created_by_id=ctx.user_id,
        items=lines,
    )
    _apply_header(quotation, data, totals)
    session.add(quotation)
    invitation.status = RfqVendorStatus.QUOTED.value
    invitation.responded_at = utcnow()
    await session.flush()
    return quotation


async def _get_for_update(
    session: AsyncSession, ctx: AccessContext, quotation_id: UUID
) -> VendorQuotation:
    quotation = await repository(session).get_for_update(ctx, PERM_VIEW, quotation_id)
    await session.refresh(quotation, attribute_names=["items"])
    return quotation


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    quotation_id: UUID,
    data: QuotationInput,
    *,
    expected_version: int | None,
) -> VendorQuotation:
    quotation = await _get_for_update(session, ctx, quotation_id)
    _check_version(quotation, expected_version)
    assert_in_scope(
        ctx,
        PERM_RECORD,
        company_id=quotation.company_id,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        entity="Quotation",
    )
    if quotation.status not in _OPEN:
        raise BusinessRuleError(
            "quotation_locked",
            f"{quotation.quotation_number} is {quotation.status.lower()} and can no longer be "
            "edited."
            + (
                " Withdraw the selection first."
                if quotation.status == QuotationStatus.SELECTED.value
                else ""
            ),
        )
    if data.vendor_id != quotation.vendor_id:
        raise ValidationError(
            "A quotation's vendor cannot be changed.",
            errors=[{"field": "vendor_id", "code": "invalid", "message": "cannot be changed"}],
        )
    rfq = await _rfq_for(session, ctx, quotation.rfq_id)
    if rfq.status != RfqStatus.ISSUED.value:
        raise BusinessRuleError("rfq_not_issued", f"{rfq.rfq_number} is no longer open.")
    lines, totals = _build_lines(ctx, rfq, data)
    _apply_header(quotation, data, totals)
    quotation.items.clear()
    await session.flush()
    quotation.items.extend(lines)
    quotation.updated_by_id = ctx.user_id
    quotation.version += 1
    await session.flush()
    return quotation


async def shortlist(
    session: AsyncSession, ctx: AccessContext, quotation_id: UUID
) -> VendorQuotation:
    quotation = await _get_for_update(session, ctx, quotation_id)
    assert_in_scope(
        ctx,
        PERM_RECORD,
        company_id=quotation.company_id,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        entity="Quotation",
    )
    if quotation.status != QuotationStatus.RECEIVED.value:
        raise BusinessRuleError(
            "quotation_not_received",
            f"{quotation.quotation_number} is {quotation.status.lower()}; only a received "
            "quotation can be shortlisted.",
        )
    quotation.status = QuotationStatus.SHORTLISTED.value
    quotation.version += 1
    await session.flush()
    return quotation


async def reject(
    session: AsyncSession, ctx: AccessContext, quotation_id: UUID, reason: str
) -> VendorQuotation:
    quotation = await _get_for_update(session, ctx, quotation_id)
    assert_in_scope(
        ctx,
        PERM_SELECT,
        company_id=quotation.company_id,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        entity="Quotation",
    )
    if quotation.status not in _OPEN:
        raise BusinessRuleError(
            "quotation_locked",
            f"{quotation.quotation_number} is {quotation.status.lower()}; "
            "only an unselected quotation can be rejected.",
        )
    quotation.status = QuotationStatus.REJECTED.value
    quotation.reject_reason = reason.strip()
    quotation.version += 1
    await record_audit(
        session,
        action=AuditAction.REJECT,
        entity_type="VendorQuotation",
        entity_id=quotation.id,
        entity_label=quotation.quotation_number,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        summary=f"{quotation.quotation_number} rejected: {reason.strip()}",
    )
    await session.flush()
    return quotation


async def has_selected(session: AsyncSession, rfq_id: UUID) -> bool:
    return (
        await session.scalar(
            select(VendorQuotation.id).where(
                VendorQuotation.rfq_id == rfq_id,
                VendorQuotation.status == QuotationStatus.SELECTED.value,
            )
        )
    ) is not None


async def live_order_for(session: AsyncSession, quotation_id: UUID) -> PurchaseOrder | None:
    """The purchase order still standing for a quotation, if any."""
    return (
        (
            await session.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.quotation_id == quotation_id,
                    PurchaseOrder.status.notin_(_DEAD_PO),
                )
            )
        )
        .scalars()
        .first()
    )


async def select_quotation(
    session: AsyncSession,
    ctx: AccessContext,
    quotation_id: UUID,
    reason: str,
    *,
    expected_version: int | None,
) -> VendorQuotation:
    """Choose the winning quotation. The reason is mandatory and is stored, and
    written to the audit log, whatever the price."""
    quotation = await _get_for_update(session, ctx, quotation_id)
    _check_version(quotation, expected_version)
    assert_in_scope(
        ctx,
        PERM_SELECT,
        company_id=quotation.company_id,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        entity="Quotation",
    )
    if len(reason.strip()) < 10:
        raise ValidationError(
            "Say why this quotation was chosen.",
            errors=[
                {
                    "field": "reason",
                    "code": "too_short",
                    "message": "write at least a sentence: an auditor will read this later",
                }
            ],
        )
    if quotation.status not in _OPEN:
        raise BusinessRuleError(
            "quotation_not_selectable",
            f"{quotation.quotation_number} is {quotation.status.lower()} and cannot be selected.",
        )
    rfq = await _rfq_for(session, ctx, quotation.rfq_id)
    if rfq.status != RfqStatus.ISSUED.value:
        raise BusinessRuleError(
            "rfq_not_issued", f"{rfq.rfq_number} is {rfq.status.lower()}; nothing can be selected."
        )
    if quotation.valid_until is not None and quotation.valid_until < utcnow().date():
        raise BusinessRuleError(
            "quotation_expired",
            f"{quotation.quotation_number} was valid until {quotation.valid_until:%d %b %Y}. "
            "Ask the vendor to reconfirm and record the new validity date.",
        )

    # Demote any earlier winner first: the partial unique index allows only one.
    previous = (
        (
            await session.execute(
                select(VendorQuotation).where(
                    VendorQuotation.rfq_id == quotation.rfq_id,
                    VendorQuotation.status == QuotationStatus.SELECTED.value,
                )
            )
        )
        .scalars()
        .first()
    )
    if previous is not None:
        if await live_order_for(session, previous.id) is not None:
            raise BusinessRuleError(
                "quotation_ordered",
                f"{previous.quotation_number} is already selected and has a purchase order. "
                "Cancel that order before choosing a different vendor.",
            )
        _demote(previous)
        await session.flush()

    quotation.status = QuotationStatus.SELECTED.value
    quotation.selection_reason = reason.strip()
    quotation.selected_by_id = ctx.user_id
    quotation.selected_at = utcnow()
    quotation.version += 1
    await record_audit(
        session,
        action=AuditAction.APPROVE,
        entity_type="VendorQuotation",
        entity_id=quotation.id,
        entity_label=quotation.quotation_number,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        summary=f"{quotation.quotation_number} selected for {rfq.rfq_number}: {reason.strip()}",
    )
    await session.flush()
    return quotation


def _demote(quotation: VendorQuotation) -> None:
    quotation.status = QuotationStatus.SHORTLISTED.value
    quotation.selection_reason = None
    quotation.selected_by_id = None
    quotation.selected_at = None
    quotation.version += 1


async def withdraw_selection(
    session: AsyncSession, ctx: AccessContext, quotation_id: UUID
) -> VendorQuotation:
    quotation = await _get_for_update(session, ctx, quotation_id)
    assert_in_scope(
        ctx,
        PERM_SELECT,
        company_id=quotation.company_id,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        entity="Quotation",
    )
    if quotation.status != QuotationStatus.SELECTED.value:
        raise BusinessRuleError(
            "quotation_not_selected", f"{quotation.quotation_number} is not the selected quotation."
        )
    if await live_order_for(session, quotation.id) is not None:
        raise BusinessRuleError(
            "quotation_ordered",
            "A purchase order was raised from this quotation. Cancel the order first.",
        )
    previous_reason = quotation.selection_reason
    _demote(quotation)
    await record_audit(
        session,
        action=AuditAction.REOPEN,
        entity_type="VendorQuotation",
        entity_id=quotation.id,
        entity_label=quotation.quotation_number,
        project_id=quotation.project_id,
        site_id=quotation.site_id,
        summary=f"Selection of {quotation.quotation_number} withdrawn "
        f"(it had been selected because: {previous_reason})",
    )
    await session.flush()
    return quotation


async def list_quotations(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[VendorQuotation], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, quotation_id: UUID) -> VendorQuotation:
    return await repository(session).get(ctx, PERM_VIEW, quotation_id)


# -----------------------------------------------------------------------------
# Comparison
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class ComparisonCell:
    quotation_id: UUID
    vendor_id: UUID
    quantity: Decimal
    rate: Decimal
    discount_pct: Decimal
    tax_pct: Decimal
    net_rate: Decimal
    line_total: Decimal
    delivery_days: int | None
    remarks: str | None
    is_lowest: bool = False
    is_partial: bool = False


@dataclass(slots=True)
class ComparisonRow:
    rfq_item: RfqItem
    estimated_rate: Decimal | None
    cells: dict[UUID, ComparisonCell] = field(default_factory=dict)


@dataclass(slots=True)
class ComparisonColumn:
    vendor_id: UUID
    rfq_vendor_id: UUID
    invitation_status: str
    quotation: VendorQuotation | None
    lines_quoted: int = 0
    covers_all: bool = False
    is_lowest_total: bool = False


@dataclass(slots=True)
class Comparison:
    rfq: Rfq
    columns: list[ComparisonColumn]
    rows: list[ComparisonRow]


async def compare(
    session: AsyncSession,
    ctx: AccessContext,
    rfq_id: UUID,
) -> Comparison:
    """The comparison matrix. Each row also carries the purchase request's
    estimated rate, so a bid can be read against what was budgeted."""
    rfq = await rfqs.get(session, ctx, rfq_id)
    # The caller needs the quotation permission as well as the RFQ's.
    if not ctx.has(PERM_VIEW):
        raise PermissionDeniedError(PERM_VIEW)
    pr_item_ids = {i.pr_item_id for i in rfq.items if i.pr_item_id is not None}
    estimates: dict[UUID, Decimal | None] = {}
    if pr_item_ids:
        estimates = {
            row[0]: row[1]
            for row in (
                await session.execute(
                    select(PurchaseRequestItem.id, PurchaseRequestItem.estimated_rate).where(
                        PurchaseRequestItem.id.in_(list(pr_item_ids))
                    )
                )
            ).tuples()
        }
    quotations = (
        (
            await session.execute(
                select(VendorQuotation)
                .where(VendorQuotation.rfq_id == rfq.id)
                .order_by(VendorQuotation.created_at)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    by_vendor = {q.vendor_id: q for q in quotations}

    columns = [
        ComparisonColumn(
            vendor_id=v.vendor_id,
            rfq_vendor_id=v.id,
            invitation_status=v.status,
            quotation=by_vendor.get(v.vendor_id),
        )
        for v in rfq.vendors
    ]
    rows: list[ComparisonRow] = []
    for item in rfq.items:
        row = ComparisonRow(
            rfq_item=item,
            estimated_rate=estimates.get(item.pr_item_id) if item.pr_item_id else None,
        )
        for quotation in quotations:
            if quotation.status == QuotationStatus.REJECTED.value:
                # A rejected bid is out of the running; it stays on the record
                # but not in the grid, where it would draw the eye to a price
                # nobody may accept.
                continue
            line = next((i for i in quotation.items if i.rfq_item_id == item.id), None)
            if line is None:
                continue
            row.cells[quotation.vendor_id] = ComparisonCell(
                quotation_id=quotation.id,
                vendor_id=quotation.vendor_id,
                quantity=line.quantity,
                rate=line.rate,
                discount_pct=line.discount_pct,
                tax_pct=line.tax_pct,
                net_rate=pricing.net_unit_rate(line.rate, line.discount_pct),
                line_total=line.line_total,
                delivery_days=line.delivery_days,
                remarks=line.remarks,
                is_partial=line.quantity < item.quantity,
            )
        if row.cells:
            lowest = min(c.net_rate for c in row.cells.values())
            for cell in row.cells.values():
                cell.is_lowest = cell.net_rate == lowest
        rows.append(row)

    total_lines = len(rfq.items)
    for column in columns:
        q = column.quotation
        if q is None or q.status == QuotationStatus.REJECTED.value:
            continue
        column.lines_quoted = len(q.items)
        column.covers_all = len(q.items) == total_lines and all(
            i.quantity == next(r.rfq_item.quantity for r in rows if r.rfq_item.id == i.rfq_item_id)
            for i in q.items
        )
    # "Cheapest total" is meaningful only between bids for the same thing: a
    # vendor who quoted one line of five would otherwise win on price.
    complete = [c for c in columns if c.covers_all and c.quotation is not None]
    if complete:
        lowest_total = min(c.quotation.total_amount for c in complete if c.quotation)
        for c in complete:
            c.is_lowest_total = c.quotation is not None and c.quotation.total_amount == lowest_total
    return Comparison(rfq=rfq, columns=columns, rows=rows)
