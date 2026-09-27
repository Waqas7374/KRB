"""Trial balance and general ledger (docs/02 §8, docs/02 §11).

Both are read straight from `journal_entry_lines` for **posted** entries only
— a draft or a pending manual entry has not happened yet, as far as the books
are concerned, and a reversed entry's own lines are cancelled out by its
reversal's lines rather than excluded, so the ledger stays a complete record
of everything that was ever posted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import PermissionDeniedError
from app.modules.finance.domain.enums import JournalStatus, NormalBalance
from app.modules.finance.models import Account, JournalEntry, JournalEntryLine
from app.modules.finance.services.journal_entries import PERM_VIEW


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    account_id: UUID
    code: str
    name: str
    account_type: str
    debit: Decimal
    credit: Decimal


def _posted_lines(company_id: UUID, as_of: date | None) -> Select[Any]:
    stmt = (
        select(JournalEntryLine)
        .join(JournalEntry, JournalEntry.id == JournalEntryLine.je_id)
        .where(
            JournalEntry.company_id == company_id, JournalEntry.status == JournalStatus.POSTED.value
        )
    )
    if as_of is not None:
        stmt = stmt.where(JournalEntry.entry_date <= as_of)
    return stmt


async def trial_balance(
    session: AsyncSession, ctx: AccessContext, *, as_of: date | None = None
) -> list[TrialBalanceRow]:
    """Every account with a non-zero movement, net onto whichever side the
    balance actually falls — a payable that has swung into debit still shows
    as a small debit, which is the point of a trial balance: it shows what is
    actually there, not what convention expects."""
    if not ctx.has(PERM_VIEW):
        raise PermissionDeniedError(PERM_VIEW)
    lines = _posted_lines(ctx.company_id, as_of).subquery()
    rows = await session.execute(
        select(
            Account.id,
            Account.code,
            Account.name,
            Account.account_type,
            func.coalesce(func.sum(lines.c.debit), 0),
            func.coalesce(func.sum(lines.c.credit), 0),
        )
        .select_from(Account)
        .outerjoin(lines, lines.c.account_id == Account.id)
        .where(Account.company_id == ctx.company_id, Account.is_postable.is_(True))
        .group_by(Account.id)
        .having(
            func.coalesce(func.sum(lines.c.debit), 0) != func.coalesce(func.sum(lines.c.credit), 0)
        )
        .order_by(Account.code)
    )
    out: list[TrialBalanceRow] = []
    for account_id, code, name, account_type, debit, credit in rows.tuples().all():
        # Which column a balance falls in is decided by the sign of the net
        # movement alone, never by the account's own normal side.
        net = Decimal(debit) - Decimal(credit)
        out.append(
            TrialBalanceRow(
                account_id, code, name, account_type, max(net, Decimal(0)), max(-net, Decimal(0))
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class LedgerRow:
    id: UUID
    je_id: UUID
    je_number: str
    entry_date: date
    description: str
    line_description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


@dataclass(frozen=True, slots=True)
class AccountLedger:
    account: Account
    opening_balance: Decimal
    rows: list[LedgerRow]
    closing_balance: Decimal


def _signed(account: Account, debit: Decimal, credit: Decimal) -> Decimal:
    sign = 1 if account.normal_balance == NormalBalance.DEBIT.value else -1
    return sign * (debit - credit)


async def general_ledger(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    account_id: UUID,
    from_date: date | None,
    to_date: date | None,
) -> AccountLedger | None:
    """One account's posted movements in date order, with a running balance —
    carried forward from whatever moved before `from_date` — on the side its
    normal balance falls."""
    if not ctx.has(PERM_VIEW):
        raise PermissionDeniedError(PERM_VIEW)
    account = await session.get(Account, account_id)
    if account is None or account.company_id != ctx.company_id:
        return None

    opening = Decimal(0)
    if from_date is not None:
        before = await session.execute(
            _posted_lines(ctx.company_id, None)
            .where(JournalEntryLine.account_id == account_id, JournalEntry.entry_date < from_date)
            .with_only_columns(
                func.coalesce(func.sum(JournalEntryLine.debit), 0),
                func.coalesce(func.sum(JournalEntryLine.credit), 0),
            )
        )
        prior_debit, prior_credit = before.one()
        opening = _signed(account, Decimal(prior_debit), Decimal(prior_credit))

    stmt = _posted_lines(ctx.company_id, to_date).where(JournalEntryLine.account_id == account_id)
    if from_date is not None:
        stmt = stmt.where(JournalEntry.entry_date >= from_date)
    # `_posted_lines` already joins JournalEntry; just add its columns to what comes back.
    stmt = stmt.add_columns(
        JournalEntry.id, JournalEntry.je_number, JournalEntry.entry_date, JournalEntry.description
    ).order_by(JournalEntry.entry_date, JournalEntry.je_number, JournalEntryLine.line_no)
    rows = (await session.execute(stmt)).all()
    running = opening
    out: list[LedgerRow] = []
    for line, je_id, je_number, entry_date, je_description in rows:
        running += _signed(account, line.debit, line.credit)
        out.append(
            LedgerRow(
                id=line.id,
                je_id=je_id,
                je_number=je_number,
                entry_date=entry_date,
                description=je_description,
                line_description=line.description,
                debit=line.debit,
                credit=line.credit,
                running_balance=running,
            )
        )
    return AccountLedger(
        account=account, opening_balance=opening, rows=out, closing_balance=running
    )
