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
