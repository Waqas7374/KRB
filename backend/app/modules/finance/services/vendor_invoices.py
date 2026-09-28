"""Vendor invoices: entered against what was ordered and received, matched
within tolerance, and only then posted (docs/02 §8, docs/07 §`/finance`).

**The 3-way match** (docs/02 §6: PO → GRN → Invoice → Payment). An invoice
line that names a GRN line is checked against it — quantity against what was
actually *accepted*, rate against the order's own rate if the GRN line came
from one — using the same `QTY_TOLERANCE` / `PRICE_TOLERANCE` business rules
a delivery is already checked against (`rules/services/resolver.py`), so a
company that tightens delivery tolerance and invoice tolerance is tightening
the same number. A GRN line with no order behind it (a counter purchase)
degrades the match to 2-way — there is no ordered rate to check the line
against, only what was actually received — and a line with neither an order
nor a GRN is not matched at all: a direct cost, priced and accounted for by
hand.

**Approving never re-books what a GRN already booked.** A GRN line already
posted its cost the moment it was received (Phase 4b); a 3-way-matched
invoice line's own entry only clears the accrual that posting left behind
(2110 Goods Received Not Invoiced) into a real payable (2100 Accounts
Payable) — at the *same* account the GRN credited, resolved through the
identical posting rule, so the two can never disagree about which one that
is. A 2-way-matched line (a counter purchase, which credited 2100 directly
at GRN time, no accrual to clear) posts nothing here — the GRN already
booked it in full, and this invoice exists only as the matched record of
what bill that was. A direct, unmatched line is the one kind that is new to
the ledger here, and needs its own account named by hand.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import (
    BusinessRuleError,
    DuplicateError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.pagination import PageParams
from app.core.scoping import scope_filter
from app.core.types import utcnow
from app.modules.finance.domain.enums import JournalSourceType, MatchType, VendorInvoiceStatus
from app.modules.finance.models import VendorInvoice, VendorInvoiceItem, VendorInvoiceMatch
from app.modules.finance.services import ledger as gl
from app.modules.finance.services import posting_rules
from app.modules.grn.services import grn_lookup
from app.modules.org.services import company_service
from app.modules.procurement.services import po_lookup, receiving
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.services import resolver as rule_resolver
from app.modules.vendors.services import vendor_lookup
from app.platform.numbering import DocumentType, next_number

PERM_VIEW = "finance.ap.view"
PERM_CREATE = "finance.ap.create"
PERM_MATCH = "finance.ap.match"
PERM_APPROVE = "finance.ap.approve"

_AMOUNT = Decimal("0.0001")


def repository(session: AsyncSession) -> ScopedRepository[VendorInvoice]:
    return ScopedRepository(
        session,
        VendorInvoice,
        entity_name="Vendor invoice",
        sortable={"invoice_number", "invoice_date", "due_date", "status", "created_at"},
        searchable=("invoice_number", "vendor_invoice_ref"),
        default_sort="-invoice_date",
    )


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


# -----------------------------------------------------------------------------
# Build
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvoiceItemInput:
    quantity: Decimal
    rate: Decimal
    po_item_id: UUID | None = None
    grn_item_id: UUID | None = None
    material_id: UUID | None = None
    description: str | None = None
    unit_id: UUID | None = None
    tax_code_id: UUID | None = None
    tax_pct: Decimal = Decimal(0)
    # Required only for a line with neither an order nor a GRN behind it —
    # every other line resolves its own account from the posting rule the
    # GRN line it matches already used.
    account_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class VendorInvoiceInput:
    vendor_id: UUID
    vendor_invoice_ref: str
    invoice_date: date
    purchase_order_id: UUID | None
    items: list[InvoiceItemInput]
    due_date: date | None = None
    withholding_amount: Decimal = Decimal(0)


async def _build_item(
    session: AsyncSession, ctx: AccessContext, vendor_id: UUID, line: InvoiceItemInput, index: int
) -> VendorInvoiceItem:
    prefix = f"items.{index}"
    grn_item = None
    po_item = None
    if line.grn_item_id is not None:
        grn_item = await grn_lookup.item(
            session, company_id=ctx.company_id, grn_item_id=line.grn_item_id
        )
        if grn_item is None:
            raise _fail(f"{prefix}.grn_item_id", "Unknown GRN line")
        if grn_item.vendor_id != vendor_id:
            raise _fail(f"{prefix}.grn_item_id", "That GRN line was not received from this vendor")
        if grn_item.po_item_id is not None:
            po_item = await po_lookup.item(
                session, company_id=ctx.company_id, po_item_id=grn_item.po_item_id
            )

    material_id = line.material_id or (grn_item.material_id if grn_item else None)
    amount = (line.quantity * line.rate).quantize(_AMOUNT, rounding=ROUND_HALF_UP)
    tax_amount = (amount * line.tax_pct / 100).quantize(_AMOUNT, rounding=ROUND_HALF_UP)

    if grn_item is not None:
        if po_item is not None:
            # 3-way: the account the GRN line's accrual sits in, so this
            # invoice clears exactly what that posting created.
            rule = await posting_rules.resolve_receipt_account(
                session, ctx, material_id=grn_item.material_id, is_po_backed=True
            )
            account_id: UUID | None = rule.credit_account_id
        else:
            # 2-way: the GRN already posted the full liability directly.
            account_id = None
    elif line.account_id is not None:
        account_id = line.account_id
    else:
        raise _fail(
            f"{prefix}.account_id", "A line with no order or GRN behind it needs an account"
        )

    return VendorInvoiceItem(
        line_no=index + 1,
        po_item_id=po_item.id if po_item else None,
        grn_item_id=grn_item.id if grn_item else None,
        material_id=material_id,
        description=line.description,
        quantity=line.quantity,
        unit_id=line.unit_id,
        rate=line.rate,
        tax_code_id=line.tax_code_id,
        tax_pct=line.tax_pct,
        tax_amount=tax_amount,
        amount=amount,
        account_id=account_id,
        created_by_id=ctx.user_id,
    )


async def _build_items(
    session: AsyncSession, ctx: AccessContext, vendor_id: UUID, lines: list[InvoiceItemInput]
) -> list[VendorInvoiceItem]:
    if not lines:
        raise BusinessRuleError("vendor_invoice_empty", "An invoice needs at least one line.")
    items = []
    for index, line in enumerate(lines):
        if line.quantity <= 0:
            raise _fail(f"items.{index}.quantity", "Must be more than zero")
        items.append(await _build_item(session, ctx, vendor_id, line, index))
    return items


def _retotal(invoice: VendorInvoice) -> None:
    invoice.subtotal = sum((i.amount for i in invoice.items), Decimal(0))
    invoice.tax_amount = sum((i.tax_amount for i in invoice.items), Decimal(0))
    invoice.total_amount = invoice.subtotal + invoice.tax_amount


async def _assert_ref_available(
    session: AsyncSession,
    ctx: AccessContext,
    vendor_id: UUID,
    vendor_name: str,
    ref: str,
    *,
    exclude_id: UUID | None = None,
) -> None:
    stmt = select(VendorInvoice.id).where(
        VendorInvoice.company_id == ctx.company_id,
        VendorInvoice.vendor_id == vendor_id,
        VendorInvoice.vendor_invoice_ref == ref,
    )
    if exclude_id is not None:
        stmt = stmt.where(VendorInvoice.id != exclude_id)
    if await session.scalar(stmt):
        raise DuplicateError(f"Bill from {vendor_name}", "vendor_invoice_ref", ref)


async def create(
    session: AsyncSession, ctx: AccessContext, data: VendorInvoiceInput
) -> VendorInvoice:
    if not ctx.has(PERM_CREATE):
        raise PermissionDeniedError(PERM_CREATE)
    vendor = (
        await vendor_lookup.vendors(session, company_id=ctx.company_id, vendor_ids={data.vendor_id})
    ).get(data.vendor_id)
    if vendor is None:
        raise _fail("vendor_id", "Unknown vendor")
    ref = data.vendor_invoice_ref.strip()
    if not ref:
        raise _fail("vendor_invoice_ref", "Required")
    await _assert_ref_available(session, ctx, vendor.id, vendor.name, ref)
    items = await _build_items(session, ctx, vendor.id, data.items)
    invoice = VendorInvoice(
        company_id=ctx.company_id,
        invoice_number=await next_number(
            session,
            company_id=ctx.company_id,
            doc_type=DocumentType.VENDOR_INVOICE,
            fiscal_year_start_month=await company_service.fiscal_year_start_month(
                session, ctx.company_id
            ),
        ),
        vendor_id=vendor.id,
        vendor_invoice_ref=ref,
        purchase_order_id=data.purchase_order_id,
        invoice_date=data.invoice_date,
        due_date=data.due_date or (data.invoice_date + timedelta(days=vendor.payment_terms_days)),
        withholding_amount=data.withholding_amount,
        status=VendorInvoiceStatus.DRAFT.value,
        items=items,
        created_by_id=ctx.user_id,
    )
    _retotal(invoice)
    session.add(invoice)
    await session.flush()
    return invoice


async def _get_for_update(
    session: AsyncSession, ctx: AccessContext, invoice_id: UUID, permission: str
) -> VendorInvoice:
    invoice = await repository(session).get_for_update(ctx, permission, invoice_id)
    await session.refresh(invoice, attribute_names=["items"])
    return invoice


def _check_version(invoice: VendorInvoice, expected: int | None) -> None:
    if expected is not None and invoice.version != expected:
        raise VersionConflictError(
            f"{invoice.invoice_number} was changed by someone else (version {invoice.version}, "
            f"you had {expected}). Reload and try again."
        )


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    invoice_id: UUID,
    data: VendorInvoiceInput,
    *,
    expected_version: int | None,
) -> VendorInvoice:
    invoice = await _get_for_update(session, ctx, invoice_id, PERM_CREATE)
    _check_version(invoice, expected_version)
    if not VendorInvoiceStatus(invoice.status).is_editable:
        raise BusinessRuleError(
            "vendor_invoice_not_editable",
            f"{invoice.invoice_number} is {invoice.status.lower().replace('_', ' ')}; only a "
            "draft or a disputed invoice can be edited.",
        )
    ref = data.vendor_invoice_ref.strip()
    if not ref:
        raise _fail("vendor_invoice_ref", "Required")
    if ref != invoice.vendor_invoice_ref:
        vendor = (
            await vendor_lookup.vendors(
                session, company_id=ctx.company_id, vendor_ids={invoice.vendor_id}
            )
        )[invoice.vendor_id]
        await _assert_ref_available(
            session, ctx, invoice.vendor_id, vendor.name, ref, exclude_id=invoice.id
        )
    items = await _build_items(session, ctx, invoice.vendor_id, data.items)
    invoice.vendor_invoice_ref = ref
    invoice.purchase_order_id = data.purchase_order_id
    invoice.invoice_date = data.invoice_date
    invoice.due_date = data.due_date or invoice.due_date
    invoice.withholding_amount = data.withholding_amount
    invoice.status = VendorInvoiceStatus.DRAFT.value
    invoice.decision_reason = None
    invoice.items.clear()
    await session.flush()
    invoice.items.extend(items)
    _retotal(invoice)
    invoice.updated_by_id = ctx.user_id
    invoice.version += 1
    await session.flush()
    return invoice


async def delete_draft(session: AsyncSession, ctx: AccessContext, invoice_id: UUID) -> None:
    invoice = await _get_for_update(session, ctx, invoice_id, PERM_CREATE)
    if invoice.status != VendorInvoiceStatus.DRAFT.value:
        raise BusinessRuleError(
            "vendor_invoice_not_draft",
            f"{invoice.invoice_number} is not a draft; it cannot be deleted.",
        )
    await session.delete(invoice)
    await session.flush()


# -----------------------------------------------------------------------------
# The 3-way match
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Check:
    within_tolerance: bool
    qty_variance: Decimal | None
    rate_variance: Decimal | None
    amount_variance: Decimal | None
    tolerance_rule_id: UUID | None


async def _tolerance(
    session: AsyncSession, ctx: AccessContext, rule_type: RuleType, on: date
) -> tuple[Decimal, Decimal, UUID | None]:
    """(pct, abs, rule id) for a tolerance type — 0/0/None, always passing,
    when nothing is configured: a check with no rule simply does not flag,
    the same principle a delivery's own checks already follow."""
    rule = await rule_resolver.resolve(
        session, company_id=ctx.company_id, rule_type=rule_type, context={}, at=on
    )
    if rule is None:
        return Decimal(0), Decimal(0), None
    pct = Decimal(str(rule.value.get("pct", 0)))
    abs_ = Decimal(str(rule.value.get("abs", 0)))
    return pct, abs_, rule.id


async def _check_item(
    session: AsyncSession, ctx: AccessContext, item: VendorInvoiceItem, on: date
) -> tuple[_Check, str]:
    if item.grn_item_id is None:
        return (
            _Check(
                within_tolerance=True,
                qty_variance=None,
                rate_variance=None,
                amount_variance=None,
                tolerance_rule_id=None,
            ),
            MatchType.UNMATCHED.value,
        )
    grn_item = await grn_lookup.item(
        session, company_id=ctx.company_id, grn_item_id=item.grn_item_id
    )
    if grn_item is None:
        raise NotFoundError("GRN line", item.grn_item_id)
    po_item = (
        await po_lookup.item(session, company_id=ctx.company_id, po_item_id=item.po_item_id)
        if item.po_item_id
        else None
    )
    match_type = MatchType.THREE_WAY.value if po_item else MatchType.TWO_WAY.value

    qty_pct, qty_abs, qty_rule_id = await _tolerance(session, ctx, RuleType.QTY_TOLERANCE, on)
    qty_variance = item.quantity - grn_item.accepted_quantity
    qty_ok = abs(qty_variance) <= max(qty_abs, grn_item.accepted_quantity * qty_pct / 100)

    reference_rate = po_item.rate if po_item else (grn_item.unit_cost or Decimal(0))
    rate_pct, _rate_abs, rate_rule_id = await _tolerance(session, ctx, RuleType.PRICE_TOLERANCE, on)
    rate_variance = item.rate - reference_rate
    rate_ok = abs(rate_variance) <= reference_rate * rate_pct / 100

    amount_variance = item.amount - (reference_rate * grn_item.accepted_quantity).quantize(
        _AMOUNT, rounding=ROUND_HALF_UP
    )
    return (
        _Check(
            within_tolerance=qty_ok and rate_ok,
            qty_variance=qty_variance,
            rate_variance=rate_variance,
            amount_variance=amount_variance,
            tolerance_rule_id=qty_rule_id or rate_rule_id,
        ),
        match_type,
    )


async def match(
    session: AsyncSession, ctx: AccessContext, invoice_id: UUID, *, expected_version: int | None
) -> VendorInvoice:
    invoice = await _get_for_update(session, ctx, invoice_id, PERM_MATCH)
    _check_version(invoice, expected_version)
    if not VendorInvoiceStatus(invoice.status).is_editable:
        raise BusinessRuleError(
            "vendor_invoice_not_matchable",
            f"{invoice.invoice_number} is {invoice.status.lower().replace('_', ' ')}; only a "
            "draft or a disputed invoice can be (re)matched.",
        )
    await session.execute(
        delete(VendorInvoiceMatch).where(
            VendorInvoiceMatch.invoice_item_id.in_([i.id for i in invoice.items])
        )
    )
    all_ok = True
    for item in invoice.items:
        check, match_type = await _check_item(session, ctx, item, invoice.invoice_date)
        all_ok = all_ok and check.within_tolerance
        session.add(
            VendorInvoiceMatch(
                invoice_item_id=item.id,
                po_item_id=item.po_item_id,
                grn_item_id=item.grn_item_id,
                match_type=match_type,
                qty_variance=check.qty_variance,
                rate_variance=check.rate_variance,
                amount_variance=check.amount_variance,
                within_tolerance=check.within_tolerance,
                tolerance_rule_id=check.tolerance_rule_id,
                created_by_id=ctx.user_id,
            )
        )
    invoice.status = (
        VendorInvoiceStatus.MATCHED.value if all_ok else VendorInvoiceStatus.DISPUTED.value
    )
    invoice.matched_at = utcnow()
    invoice.decision_reason = None if all_ok else "One or more lines are outside tolerance."
    invoice.version += 1
    invoice.updated_by_id = ctx.user_id
    await session.flush()
    return invoice


async def dispute(
    session: AsyncSession,
    ctx: AccessContext,
    invoice_id: UUID,
    reason: str,
    *,
    expected_version: int | None,
) -> VendorInvoice:
    invoice = await _get_for_update(session, ctx, invoice_id, PERM_APPROVE)
    _check_version(invoice, expected_version)
    if invoice.status != VendorInvoiceStatus.MATCHED.value:
        raise BusinessRuleError(
            "vendor_invoice_not_matched", f"{invoice.invoice_number} is not matched."
        )
    if len((reason or "").strip()) < 5:
        raise BusinessRuleError("reason_required", "Say why this invoice is disputed.")
    invoice.status = VendorInvoiceStatus.DISPUTED.value
    invoice.decision_reason = reason.strip()
    invoice.version += 1
    invoice.updated_by_id = ctx.user_id
    await session.flush()
    return invoice


# -----------------------------------------------------------------------------
# Approval and posting
# -----------------------------------------------------------------------------


async def approve(
    session: AsyncSession, ctx: AccessContext, invoice_id: UUID, *, expected_version: int | None
) -> VendorInvoice:
    invoice = await _get_for_update(session, ctx, invoice_id, PERM_APPROVE)
    _check_version(invoice, expected_version)
    if invoice.status != VendorInvoiceStatus.MATCHED.value:
        raise BusinessRuleError(
            "vendor_invoice_not_matched",
            f"{invoice.invoice_number} is {invoice.status.lower().replace('_', ' ')}; only a "
            "matched invoice can be approved.",
        )
    debit_totals: dict[UUID, Decimal] = defaultdict(Decimal)
    posted_total = Decimal(0)
    for item in invoice.items:
        if item.account_id is None:
            continue  # a 2-way-matched line: the GRN already booked it in full
        line_total = item.amount + item.tax_amount
        debit_totals[item.account_id] += line_total
        posted_total += line_total
        if item.po_item_id is not None:
            await receiving.apply_invoice(
                session, po_item_id=item.po_item_id, invoiced=item.quantity
            )

    if debit_totals:
        payable_rule = await posting_rules.resolve(
            session, ctx, source_type=JournalSourceType.INVOICE, event="PAYABLE", context={}
        )
        if payable_rule is None:
            raise BusinessRuleError(
                "posting_rule_missing",
                "No posting rule is set up for vendor invoices. Ask finance to add one under "
                "Posting Rules before this can post.",
            )
        dims = {"vendor_id": invoice.vendor_id}
        lines = [
            gl.debit(account_id, amount, **dims) for account_id, amount in debit_totals.items()
        ]
        lines.append(gl.credit(payable_rule.credit_account_id, posted_total, **dims))
        entry = await gl.post_system_entry(
            session,
            ctx,
            source_type=JournalSourceType.INVOICE,
            source_id=invoice.id,
            entry_date=invoice.invoice_date,
            description=f"Vendor invoice: {invoice.invoice_number} ({invoice.vendor_invoice_ref})",
            reference=invoice.vendor_invoice_ref,
            lines=lines,
        )
        invoice.journal_entry_id = entry.id

    invoice.status = VendorInvoiceStatus.APPROVED.value
    invoice.approved_at = utcnow()
    invoice.approved_by_id = ctx.user_id
    invoice.decision_reason = None
    invoice.version += 1
    invoice.updated_by_id = ctx.user_id
    await session.flush()
    return invoice


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


async def list_invoices(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[VendorInvoice], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, invoice_id: UUID) -> VendorInvoice:
    invoice = await repository(session).get(ctx, PERM_VIEW, invoice_id)
    await session.refresh(invoice, attribute_names=["items"])
    return invoice


@dataclass(frozen=True, slots=True)
class AgeingBucket:
    vendor_id: UUID
    vendor_name: str
    current: Decimal
    days_1_30: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    days_over_90: Decimal
    total: Decimal


async def payables_aging(
    session: AsyncSession, ctx: AccessContext, *, as_of: date
) -> list[AgeingBucket]:
    """Every vendor with something still owed, approved invoices only —
    a draft or a disputed one is not yet a real obligation — bucketed by how
    many days past its due date `as_of` falls."""
    if not ctx.has(PERM_VIEW):
        raise PermissionDeniedError(PERM_VIEW)
    stmt = scope_filter(select(VendorInvoice), VendorInvoice, ctx, PERM_VIEW).where(
        VendorInvoice.status.in_(
            (
                VendorInvoiceStatus.APPROVED.value,
                VendorInvoiceStatus.PARTIALLY_PAID.value,
            )
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    vendor_ids = {r.vendor_id for r in rows}
    vendors = await vendor_lookup.vendors(session, company_id=ctx.company_id, vendor_ids=vendor_ids)
    buckets: dict[UUID, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 5)
    for row in rows:
        outstanding = row.total_amount - row.paid_amount
        if outstanding <= 0:
            continue
        days_over = (as_of - row.due_date).days
        slot = (
            0
            if days_over <= 0
            else 1
            if days_over <= 30
            else 2
            if days_over <= 60
            else 3
            if days_over <= 90
            else 4
        )
        buckets[row.vendor_id][slot] += outstanding
    return [
        AgeingBucket(
            vendor_id=vendor_id,
            vendor_name=vendors[vendor_id].name if vendor_id in vendors else "",
            current=values[0],
            days_1_30=values[1],
            days_31_60=values[2],
            days_61_90=values[3],
            days_over_90=values[4],
            total=sum(values, Decimal(0)),
        )
        for vendor_id, values in buckets.items()
    ]
