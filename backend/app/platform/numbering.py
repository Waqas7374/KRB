"""Gapless document numbering.

`PR-2026-00001`, `PO-2026-00184`, `GRN-2026-00092`.

A PostgreSQL sequence is deliberately *not* used: sequences leave gaps when a
transaction rolls back, and an auditor asking "where is PO-2026-00183?" wants an
answer better than "the database skipped it". Instead a counter row is locked
with `SELECT ... FOR UPDATE` and incremented in the same transaction as the
document, so the number and the document commit or vanish together.

The cost is serialisation on that row per document type. At the volumes in
docs/01 §6 (thousands of documents a day, not thousands a second) that is the
right trade.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.types import today_utc, uuid7
from app.platform.models import DocumentSequence


class DocumentType:
    """Document-type codes and their number prefixes.

    Registered centrally so two modules cannot pick the same prefix, and so the
    seed can create every counter up front.
    """

    PURCHASE_REQUEST = "purchase_request"
    RFQ = "rfq"
    QUOTATION = "quotation"
    PURCHASE_ORDER = "purchase_order"
    DELIVERY = "delivery"
    GRN = "grn"
    STOCK_ISSUE = "stock_issue"
    STOCK_TRANSFER = "stock_transfer"
    STOCK_ADJUSTMENT = "stock_adjustment"
    JOURNAL_ENTRY = "journal_entry"
    VENDOR_INVOICE = "vendor_invoice"
    PAYMENT_REQUEST = "payment_request"
    PAYMENT = "payment"
    CUSTOMER_INVOICE = "customer_invoice"
    RECEIPT = "receipt"
    VENDOR = "vendor"
    EMPLOYEE = "employee"
    BUDGET = "budget"


# doc_type -> (prefix, resets each fiscal year?)
PREFIXES: dict[str, tuple[str, bool]] = {
    DocumentType.PURCHASE_REQUEST: ("PR", True),
    DocumentType.RFQ: ("RFQ", True),
    DocumentType.QUOTATION: ("QT", True),
    DocumentType.PURCHASE_ORDER: ("PO", True),
    DocumentType.DELIVERY: ("DLV", True),
    DocumentType.GRN: ("GRN", True),
    DocumentType.STOCK_ISSUE: ("ISS", True),
    DocumentType.STOCK_TRANSFER: ("TRF", True),
    DocumentType.STOCK_ADJUSTMENT: ("ADJ", True),
    DocumentType.JOURNAL_ENTRY: ("JV", True),
    DocumentType.VENDOR_INVOICE: ("VINV", True),
    DocumentType.PAYMENT_REQUEST: ("PREQ", True),
    DocumentType.PAYMENT: ("PMT", True),
    DocumentType.CUSTOMER_INVOICE: ("INV", True),
    DocumentType.RECEIPT: ("RCPT", True),
    # Master data numbers run continuously: a vendor's code should not change
    # meaning because the fiscal year did.
    DocumentType.VENDOR: ("VEN", False),
    DocumentType.EMPLOYEE: ("EMP", False),
    DocumentType.BUDGET: ("BUD", True),
}


class UnknownDocumentTypeError(AppError):
    status_code = 500
    error_type = "unknown-document-type"
    title = "Unknown document type"


def fiscal_year_of(on: date, start_month: int) -> int:
    """The fiscal year a date falls in, labelled by its starting calendar year.

    Pakistan runs 1 July to 30 June (decision Q1), so 2026-08-12 is FY2026 and
    2026-05-12 is FY2025.
    """
    return on.year if on.month >= start_month else on.year - 1


def format_fiscal_year(fiscal_year: int, start_month: int) -> str:
    """`2026-27` for a July-start year, `2026` for a January-start one."""
    if start_month == 1:
        return str(fiscal_year)
    return f"{fiscal_year}-{str(fiscal_year + 1)[-2:]}"


async def next_number(
    session: AsyncSession,
    *,
    company_id: UUID,
    doc_type: str,
    on: date | None = None,
    fiscal_year_start_month: int = 7,
) -> str:
    """Allocate the next document number.

    Must be called inside the transaction that creates the document. The
    counter row is locked for the remainder of that transaction, so two
    concurrent callers cannot receive the same number.
    """
    if doc_type not in PREFIXES:
        raise UnknownDocumentTypeError(
            f"'{doc_type}' has no number prefix. Register it in platform.numbering.PREFIXES."
        )

    prefix, resets_yearly = PREFIXES[doc_type]
    on = on or today_utc()
    fiscal_year = fiscal_year_of(on, fiscal_year_start_month) if resets_yearly else 0

    stmt = (
        select(DocumentSequence)
        .where(
            DocumentSequence.company_id == company_id,
            DocumentSequence.doc_type == doc_type,
            DocumentSequence.fiscal_year == fiscal_year,
        )
        .with_for_update()
    )
    counter = (await session.execute(stmt)).scalar_one_or_none()

    if counter is None:
        counter = DocumentSequence(
            id=uuid7(),
            company_id=company_id,
            doc_type=doc_type,
            fiscal_year=fiscal_year,
            prefix=prefix,
            year_token=format_fiscal_year(fiscal_year, fiscal_year_start_month)
            if resets_yearly
            else None,
            next_value=1,
            padding=5,
        )
        session.add(counter)
        await session.flush()
        # Re-lock: the row we just inserted is ours, but taking the lock
        # explicitly keeps the concurrent path and the first-use path identical.
        counter = (await session.execute(stmt)).scalar_one()

    value = counter.next_value
    counter.next_value = value + 1

    body = f"{value:0{counter.padding}d}"
    if counter.year_token:
        return f"{counter.prefix}-{counter.year_token}-{body}"
    return f"{counter.prefix}-{body}"


async def peek_number(
    session: AsyncSession,
    *,
    company_id: UUID,
    doc_type: str,
    on: date | None = None,
    fiscal_year_start_month: int = 7,
) -> str | None:
    """The number that *would* be allocated next, without consuming it.

    For a "your PO will be numbered ..." hint in the UI. Never used to set a
    document's number — that must go through `next_number`.
    """
    if doc_type not in PREFIXES:
        return None
    prefix, resets_yearly = PREFIXES[doc_type]
    on = on or today_utc()
    fiscal_year = fiscal_year_of(on, fiscal_year_start_month) if resets_yearly else 0

    counter = (
        await session.execute(
            select(DocumentSequence).where(
                DocumentSequence.company_id == company_id,
                DocumentSequence.doc_type == doc_type,
                DocumentSequence.fiscal_year == fiscal_year,
            )
        )
    ).scalar_one_or_none()

    value = counter.next_value if counter else 1
    padding = counter.padding if counter else 5
    token = (
        counter.year_token
        if counter
        else (format_fiscal_year(fiscal_year, fiscal_year_start_month) if resets_yearly else None)
    )
    body = f"{value:0{padding}d}"
    return f"{prefix}-{token}-{body}" if token else f"{prefix}-{body}"


async def ensure_sequence_at_least(
    session: AsyncSession,
    *,
    company_id: UUID,
    doc_type: str,
    minimum_next: int,
    fiscal_year: int = 0,
) -> None:
    """Advance a counter so it cannot re-issue a number already in use.

    Needed whenever rows are created with hand-written numbers — seed data, or
    a CSV import of existing vendors and employees. Without this the first
    server-allocated code collides with a migrated one, which is exactly the
    kind of failure that only shows up in front of a user.

    Never moves a counter backwards.
    """
    if doc_type not in PREFIXES:
        raise UnknownDocumentTypeError(f"'{doc_type}' has no number prefix.")

    counter = (
        await session.execute(
            select(DocumentSequence)
            .where(
                DocumentSequence.company_id == company_id,
                DocumentSequence.doc_type == doc_type,
                DocumentSequence.fiscal_year == fiscal_year,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    prefix, _resets_yearly = PREFIXES[doc_type]

    if counter is None:
        session.add(
            DocumentSequence(
                id=uuid7(),
                company_id=company_id,
                doc_type=doc_type,
                fiscal_year=fiscal_year,
                prefix=prefix,
                # A continuous sequence has no year component.
                year_token=None,
                next_value=minimum_next,
                padding=5,
            )
        )
        await session.flush()
        return

    if counter.next_value < minimum_next:
        counter.next_value = minimum_next
