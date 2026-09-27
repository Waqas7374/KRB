"""The finance domain, in isolation: which side of an account increases it,
and how a journal status describes what can still change (docs/02 §8)."""

from __future__ import annotations

from app.modules.finance.domain.enums import AccountType, JournalStatus, NormalBalance


def test_asset_expense_and_development_cost_are_debit_accounts() -> None:
    for kind in (AccountType.ASSET, AccountType.EXPENSE, AccountType.COGS_DEV_COST):
        assert kind.normal_balance is NormalBalance.DEBIT


def test_liability_equity_and_revenue_are_credit_accounts() -> None:
    for kind in (AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE):
        assert kind.normal_balance is NormalBalance.CREDIT


def test_only_a_draft_is_editable() -> None:
    assert JournalStatus.DRAFT.is_editable is True
    for status in (JournalStatus.PENDING_APPROVAL, JournalStatus.POSTED, JournalStatus.REVERSED):
        assert status.is_editable is False
