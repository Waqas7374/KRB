"""Payments: executing an approved payment request, allocating what it paid
against specific invoices, clearing and cancelling (docs/02 §8).

**Execution never happens without an approved request behind it** —
`finance.payment.execute` is deliberately a different permission from
`finance.payment.request`/`.approve`: raising money to be paid and deciding
it should be paid are one thing, actually moving it is another. A payment
executes its request in full, in one go — no partial execution of a
request; a *partial* payment against an invoice happens through allocation,
never by under-paying the request itself.

**Posting never re-books what an invoice already booked.** A vendor
invoice's own approval already turned a GRN's accrual (2110) into a payable
(2100) — or, unmatched, posted straight to its own account. A payment's own
entry only clears that payable: debit 2100 for the gross amount (the same
account an invoice's approval credited, through the identical posting
rule), credit the withholding payable if anything was retained, and credit
the paying bank account's own `gl_account_id` for what actually left it.
Debit always equals credit by construction: gross = net + withholding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageParams
from app.core.types import utcnow
from app.modules.finance.domain.enums import (
    JournalSourceType,
    PaymentDirection,
    PaymentMethod,
    PaymentRequestStatus,
    PaymentStatus,
    VendorInvoiceStatus,
)
from app.modules.finance.models import (
    BankAccount,
    Payment,
    PaymentAllocation,
    PaymentRequest,
    VendorInvoice,
)
from app.modules.finance.services import journal_entries, posting_rules
from app.modules.finance.services import ledger as gl
from app.modules.org.services import company_service
from app.platform.numbering import DocumentType, next_number

PERM_VIEW = "finance.payment.view"
PERM_EXECUTE = "finance.payment.execute"

_AMOUNT = Decimal("0.0001")


def repository(session: AsyncSession) -> ScopedRepository[Payment]:
    return ScopedRepository(
        session,
        Payment,
        entity_name="Payment",
        sortable={"payment_number", "payment_date", "status", "gross_amount", "created_at"},
        searchable=("payment_number", "instrument_no"),
        default_sort="-payment_date",
    )


def _fail(rule: str, detail: str) -> BusinessRuleError:
    return BusinessRuleError(rule, detail)


# -----------------------------------------------------------------------------
# Execute
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaymentInput:
    payment_request_id: UUID
    payment_date: date
    method: str
    bank_account_id: UUID | None
    instrument_no: str | None
    withholding_amount: Decimal = Decimal(0)


async def _locked_request(
    session: AsyncSession, ctx: AccessContext, request_id: UUID
) -> PaymentRequest:
    row = (
        await session.execute(
            select(PaymentRequest)
            .where(PaymentRequest.id == request_id, PaymentRequest.company_id == ctx.company_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Payment request", request_id)
    return row


async def _locked_bank_account(
    session: AsyncSession, ctx: AccessContext, bank_account_id: UUID
) -> BankAccount:
    row = (
        await session.execute(
            select(BankAccount).where(
                BankAccount.id == bank_account_id, BankAccount.company_id == ctx.company_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise _fail("bank_account_unknown", "That bank account could not be found.")
    if not row.is_active:
        raise _fail("bank_account_inactive", f"{row.account_title} is not active.")
    return row


async def create(session: AsyncSession, ctx: AccessContext, data: PaymentInput) -> Payment:
    if not ctx.has(PERM_EXECUTE):
        raise PermissionDeniedError(PERM_EXECUTE)
    try:
        method = PaymentMethod(data.method)
    except ValueError as exc:
        raise _fail("payment_method_invalid", f"'{data.method}' is not a known method.") from exc

    request = await _locked_request(session, ctx, data.payment_request_id)
    if request.status != PaymentRequestStatus.APPROVED.value:
        raise _fail(
            "payment_request_not_approved",
            f"{request.request_number} is {request.status.lower().replace('_', ' ')}; only an "
            "approved request can be paid.",
        )
    if data.withholding_amount < 0 or data.withholding_amount > request.amount:
        raise _fail(
            "withholding_amount_invalid", "Withholding cannot be negative or exceed the amount."
        )

    if data.bank_account_id is None:
        raise _fail(
            "bank_account_required",
            "Name the account this payment actually leaves — a cash till is kept as its own "
            "bank account row too, so every method names one.",
        )
    bank_account = await _locked_bank_account(session, ctx, data.bank_account_id)

    gross = request.amount
    net = (gross - data.withholding_amount).quantize(_AMOUNT)

    number = await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DocumentType.PAYMENT,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )
    payment = Payment(
        company_id=ctx.company_id,
        payment_number=number,
        payment_date=data.payment_date,
        payment_request_id=request.id,
        vendor_id=request.vendor_id,
        direction=PaymentDirection.OUT.value,
        method=method.value,
        bank_account_id=bank_account.id if bank_account else None,
        instrument_no=data.instrument_no,
        gross_amount=gross,
        withholding_amount=data.withholding_amount,
        net_amount=net,
        status=PaymentStatus.ISSUED.value,
        created_by_id=ctx.user_id,
    )
    session.add(payment)
    await session.flush()

    payable_rule = await posting_rules.resolve(
        session, ctx, source_type=JournalSourceType.PAYMENT, event="EXECUTE", context={}
    )
    if payable_rule is None:
        raise _fail(
            "posting_rule_missing",
            "No posting rule is set up for payments. Ask finance to add one under Posting Rules "
            "before this can post.",
        )
    dims: dict[str, Any] = {"vendor_id": request.vendor_id}
    lines = [gl.debit(payable_rule.debit_account_id, gross, **dims)]
    # A line's own CHECK constraint requires a positive debit or credit,
    # never zero — skip a leg entirely rather than post a zero-amount line
    # (net is zero only when withholding retains the whole gross amount).
    if net > 0:
        lines.append(gl.credit(bank_account.gl_account_id, net, **dims))
    if data.withholding_amount > 0:
        lines.append(gl.credit(payable_rule.credit_account_id, data.withholding_amount, **dims))

    entry = await gl.post_system_entry(
        session,
        ctx,
        source_type=JournalSourceType.PAYMENT,
        source_id=payment.id,
        entry_date=data.payment_date,
        description=f"Payment {number} to vendor",
        reference=data.instrument_no,
        lines=lines,
    )
    payment.journal_entry_id = entry.id
    request.status = PaymentRequestStatus.PAID.value
    request.updated_by_id = ctx.user_id
    request.version += 1
    await session.flush()
    return payment


# -----------------------------------------------------------------------------
# Allocate
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocationInput:
    invoice_id: UUID
    allocated_amount: Decimal


async def _locked_payment(session: AsyncSession, ctx: AccessContext, payment_id: UUID) -> Payment:
    row = (
        await session.execute(
            select(Payment)
            .where(Payment.id == payment_id, Payment.company_id == ctx.company_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Payment", payment_id)
    await session.refresh(row, attribute_names=["allocations"])
    return row


async def allocate(
    session: AsyncSession, ctx: AccessContext, payment_id: UUID, items: list[AllocationInput]
) -> Payment:
    if not ctx.has(PERM_EXECUTE):
        raise PermissionDeniedError(PERM_EXECUTE)
    if not items:
        raise _fail("payment_allocation_empty", "Name at least one invoice to allocate against.")
    payment = await _locked_payment(session, ctx, payment_id)
    if payment.status != PaymentStatus.ISSUED.value:
        raise _fail(
            "payment_not_issued",
            f"{payment.payment_number} is {payment.status.lower()}; only an issued payment can "
            "be allocated.",
        )
    already = {a.invoice_id for a in payment.allocations}
    batch_total = sum((i.allocated_amount for i in items), Decimal(0))
    if payment.allocated_amount + batch_total > payment.net_amount:
        raise _fail(
            "payment_allocation_exceeds_net",
            f"That would allocate more than {payment.payment_number}'s net amount "
            f"({payment.net_amount}).",
        )

    for item in items:
        if item.allocated_amount <= 0:
            raise _fail("payment_allocation_amount", "Each allocation must be more than zero.")
        if item.invoice_id in already:
            raise _fail(
                "payment_allocation_duplicate",
                "This payment already has an allocation against that invoice.",
            )
        invoice = (
            await session.execute(
                select(VendorInvoice)
                .where(
                    VendorInvoice.id == item.invoice_id, VendorInvoice.company_id == ctx.company_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if invoice is None:
            raise _fail("payment_allocation_invoice_unknown", "That invoice could not be found.")
        if invoice.vendor_id != payment.vendor_id:
            raise _fail(
                "payment_allocation_vendor_mismatch",
                f"{invoice.invoice_number} belongs to a different vendor than this payment.",
            )
        if invoice.status not in (
            VendorInvoiceStatus.APPROVED.value,
            VendorInvoiceStatus.PARTIALLY_PAID.value,
        ):
            raise _fail(
                "payment_allocation_invoice_not_payable",
                f"{invoice.invoice_number} is {invoice.status.lower().replace('_', ' ')} and "
                "cannot be allocated against.",
            )
        outstanding = invoice.total_amount - invoice.paid_amount
        if item.allocated_amount > outstanding:
            raise _fail(
                "payment_allocation_exceeds_outstanding",
                f"That would pay more of {invoice.invoice_number} than is outstanding "
                f"({outstanding}).",
            )
        invoice.paid_amount += item.allocated_amount
        invoice.status = (
            VendorInvoiceStatus.PAID.value
            if invoice.paid_amount >= invoice.total_amount
            else VendorInvoiceStatus.PARTIALLY_PAID.value
        )
        invoice.updated_by_id = ctx.user_id
        invoice.version += 1
        session.add(
            PaymentAllocation(
                payment_id=payment.id,
                invoice_id=item.invoice_id,
                allocated_amount=item.allocated_amount,
                created_by_id=ctx.user_id,
            )
        )
        already.add(item.invoice_id)

    payment.allocated_amount += batch_total
    payment.updated_by_id = ctx.user_id
    payment.version += 1
    await session.flush()
    await session.refresh(payment, attribute_names=["allocations"])
    return payment


# -----------------------------------------------------------------------------
# Clear / cancel
# -----------------------------------------------------------------------------


async def mark_cleared(session: AsyncSession, ctx: AccessContext, payment_id: UUID) -> Payment:
    if not ctx.has(PERM_EXECUTE):
        raise PermissionDeniedError(PERM_EXECUTE)
    payment = await _locked_payment(session, ctx, payment_id)
    if payment.status != PaymentStatus.ISSUED.value:
        raise _fail(
            "payment_not_issued",
            f"{payment.payment_number} is {payment.status.lower()}; only an issued payment can "
            "be marked cleared.",
        )
    payment.status = PaymentStatus.CLEARED.value
    payment.cleared_at = utcnow()
    payment.updated_by_id = ctx.user_id
    payment.version += 1
    await session.flush()
    return payment


async def cancel(
    session: AsyncSession, ctx: AccessContext, payment_id: UUID, reason: str
) -> Payment:
    if not ctx.has(PERM_EXECUTE):
        raise PermissionDeniedError(PERM_EXECUTE)
    if len((reason or "").strip()) < 5:
        raise _fail("reason_required", "Say why this payment is being cancelled.")
    payment = await _locked_payment(session, ctx, payment_id)
    if payment.status != PaymentStatus.ISSUED.value:
        raise _fail(
            "payment_not_cancellable",
            f"{payment.payment_number} is {payment.status.lower()}; only an issued, not yet "
            "cleared payment can be cancelled.",
        )

    for allocation in list(payment.allocations):
        invoice = (
            await session.execute(
                select(VendorInvoice)
                .where(VendorInvoice.id == allocation.invoice_id)
                .with_for_update()
            )
        ).scalar_one()
        invoice.paid_amount -= allocation.allocated_amount
        invoice.status = (
            VendorInvoiceStatus.PARTIALLY_PAID.value
            if invoice.paid_amount > 0
            else VendorInvoiceStatus.APPROVED.value
        )
        invoice.updated_by_id = ctx.user_id
        invoice.version += 1
        await session.delete(allocation)

    if payment.journal_entry_id is not None:
        await journal_entries.reverse_system(
            session,
            ctx,
            payment.journal_entry_id,
            f"Payment {payment.payment_number} cancelled: {reason.strip()}",
        )

    if payment.payment_request_id is not None:
        request = await _locked_request(session, ctx, payment.payment_request_id)
        request.status = PaymentRequestStatus.APPROVED.value
        request.updated_by_id = ctx.user_id
        request.version += 1

    payment.status = PaymentStatus.CANCELLED.value
    payment.cancelled_at = utcnow()
    payment.cancel_reason = reason.strip()
    payment.allocated_amount = Decimal(0)
    payment.updated_by_id = ctx.user_id
    payment.version += 1
    await session.flush()
    return payment


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


async def list_payments(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[Payment], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, payment_id: UUID) -> Payment:
    payment = await repository(session).get(ctx, PERM_VIEW, payment_id)
    await session.refresh(payment, attribute_names=["allocations"])
    return payment
