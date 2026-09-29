"""Finance enumerations (docs/02 §8, docs/12 Q2)."""

from __future__ import annotations

from enum import StrEnum


class AccountType(StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"
    # Development cost accumulates as an asset (work in progress on the land)
    # rather than hitting the income statement as it is spent — a land
    # developer's revenue is recognised on sale, not as costs are incurred.
    COGS_DEV_COST = "COGS_DEV_COST"

    @property
    def normal_balance(self) -> NormalBalance:
        """The side that increases this account. There are no contra accounts
        in v1; an account that needs one is a Phase-4-hardening addition, not
        a schema change (`accounts.normal_balance` is already its own column)."""
        return (
            NormalBalance.CREDIT
            if self in (AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE)
            else NormalBalance.DEBIT
        )


class NormalBalance(StrEnum):
    DEBIT = "DR"
    CREDIT = "CR"


class PeriodStatus(StrEnum):
    OPEN = "OPEN"
    # No new postings; an OPEN period can be closed and a CLOSED one reopened.
    CLOSED = "CLOSED"
    # Closed for good (after year-end roll-forward). Never reopened.
    LOCKED = "LOCKED"


class JournalSourceType(StrEnum):
    MANUAL = "MANUAL"
    GRN = "GRN"
    INVENTORY = "INVENTORY"
    INVOICE = "INVOICE"
    PAYMENT = "PAYMENT"
    PAYROLL = "PAYROLL"
    DEPRECIATION = "DEPRECIATION"
    # A dedicated source for loading balances at go-live (docs/12 Q2), posted
    # into a designated opening period and reconciled to a signed trial balance.
    OPENING_BALANCE = "OPENING_BALANCE"


class JournalStatus(StrEnum):
    # Editable: fresh, or sent back by an approver (their note is kept as
    # decision_reason). There is no separate REJECTED status — a rejected
    # manual entry is a draft again, exactly like a stock adjustment.
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    POSTED = "POSTED"
    REVERSED = "REVERSED"

    @property
    def is_editable(self) -> bool:
        return self is JournalStatus.DRAFT


class BudgetStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    # Entered automatically the first time a line's revised_amount is set on
    # an approved budget (see finance/services/budgets.py) — not a status
    # anyone chooses directly.
    REVISED = "REVISED"
    CLOSED = "CLOSED"

    @property
    def is_editable(self) -> bool:
        return self is BudgetStatus.DRAFT


class CommitmentSourceType(StrEnum):
    PO = "PO"
    CONTRACT = "CONTRACT"


class CommitmentStatus(StrEnum):
    OPEN = "OPEN"
    PARTIALLY_RELEASED = "PARTIALLY_RELEASED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class TaxType(StrEnum):
    SALES_TAX = "SALES_TAX"
    # Deducted from what a vendor is *paid*, not from what they are owed — a
    # tax_codes row of this type carries a section_code (docs/12 Q1) and only
    # ever affects payments.withholding_amount (Phase 4d), never the invoice's
    # own posting.
    WITHHOLDING = "WITHHOLDING"


class TaxAppliesTo(StrEnum):
    GOODS = "GOODS"
    SERVICES = "SERVICES"
    PAYMENT = "PAYMENT"


class VendorInvoiceStatus(StrEnum):
    # Editable: fresh, or sent back by a failed match to be corrected.
    DRAFT = "DRAFT"
    PENDING_MATCH = "PENDING_MATCH"
    MATCHED = "MATCHED"
    DISPUTED = "DISPUTED"
    APPROVED = "APPROVED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    CANCELLED = "CANCELLED"

    @property
    def is_editable(self) -> bool:
        return self in (VendorInvoiceStatus.DRAFT, VendorInvoiceStatus.DISPUTED)


class MatchType(StrEnum):
    """Whether a line matched against a purchase order as well as a GRN, or
    only a GRN — a PO-less counter purchase degrades 3-way matching to 2-way
    (docs/12 Q3): there is no ordered rate or quantity to check the line
    against, only what was actually received."""

    THREE_WAY = "THREE_WAY"
    TWO_WAY = "TWO_WAY"
    # Neither a PO nor a GRN behind it — a service or a direct expense line,
    # entered and priced by hand. Nothing to match; always within tolerance.
    UNMATCHED = "UNMATCHED"


class PaymentPriority(StrEnum):
    NORMAL = "NORMAL"
    URGENT = "URGENT"


class PaymentRequestStatus(StrEnum):
    # Editable: fresh, or sent back by a decision (rejected / changes
    # requested) to be corrected. There is no separate REJECTED-is-final —
    # like a purchase request, a rejected payment request is a draft again.
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    # Set the moment a Payment is created against this request (4d executes a
    # request in full, in one payment — see vendor_invoices' own "never
    # re-book what already posted" precedent: a paid request's money has
    # already moved, so nothing about it is editable again).
    PAID = "PAID"
    CANCELLED = "CANCELLED"

    @property
    def is_editable(self) -> bool:
        return self in (
            PaymentRequestStatus.DRAFT,
            PaymentRequestStatus.REJECTED,
            PaymentRequestStatus.CHANGES_REQUESTED,
        )


class PaymentMethod(StrEnum):
    BANK_TRANSFER = "BANK_TRANSFER"
    CHEQUE = "CHEQUE"
    # Still names a `bank_account_id` — a cash till is kept as its own row
    # (linked to 1110 Cash in Hand) so every method credits a real account
    # rather than special-casing the one that isn't a bank.
    CASH = "CASH"
    ONLINE = "ONLINE"


class PaymentDirection(StrEnum):
    # Only OUT (a vendor payment) is built in 4d; IN is docs/02's own AR
    # mirror, reserved for whichever slice actually wires a customer receipt
    # through this table rather than `receipts` (docs/02 §8 keeps the two
    # separate: customer_invoices are settled_by receipts, not payments).
    OUT = "OUT"
    IN = "IN"


class PaymentStatus(StrEnum):
    # A payment posts the moment it is issued (the same "post immediately"
    # philosophy as a GRN or a stock movement) — ISSUED is not a draft, it is
    # money that has already left the door and hit the GL; CLEARED is purely
    # the bank's own later confirmation, recorded but never re-posted.
    ISSUED = "ISSUED"
    CLEARED = "CLEARED"
    CANCELLED = "CANCELLED"
