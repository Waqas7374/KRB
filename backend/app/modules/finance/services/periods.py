"""Accounting periods (docs/02 §8, docs/12 Q1: the fiscal year starts 1 July).

Twelve are generated at once for a fiscal year, from the company's own
`fiscal_year_start_month`. A period moves OPEN → CLOSED → LOCKED and never
back past LOCKED — that is what makes a locked period's numbers a fact rather
than something a later mistake could quietly move.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.scoping import scope_filter
from app.core.types import utcnow
from app.modules.finance.domain.enums import PeriodStatus
from app.modules.finance.models import AccountingPeriod
from app.modules.org.services import company_service

PERM_VIEW = "finance.gl.view"
PERM_MANAGE = "finance.period.close"


async def generate_fiscal_year(
    session: AsyncSession, ctx: AccessContext, fiscal_year: int
) -> list[AccountingPeriod]:
    """Twelve monthly periods for one fiscal year. Safe to call again for the
    same year: existing periods are left exactly as they are."""
    if not ctx.has(PERM_MANAGE):
        raise PermissionDeniedError(PERM_MANAGE)
    start_month = await company_service.fiscal_year_start_month(session, ctx.company_id)
    existing = {
        p.period_no: p
        for p in (
            await session.execute(
                select(AccountingPeriod).where(
                    AccountingPeriod.company_id == ctx.company_id,
                    AccountingPeriod.fiscal_year == fiscal_year,
                )
            )
        )
        .scalars()
        .all()
    }
    created: list[AccountingPeriod] = []
    start = date(fiscal_year, start_month, 1)
    for period_no in range(1, 13):
        if period_no in existing:
            created.append(existing[period_no])
            continue
        period_start = start + relativedelta(months=period_no - 1)
        period_end = period_start + relativedelta(months=1) - relativedelta(days=1)
        period = AccountingPeriod(
            company_id=ctx.company_id,
            fiscal_year=fiscal_year,
            period_no=period_no,
            start_date=period_start,
            end_date=period_end,
            status=PeriodStatus.OPEN.value,
            created_by_id=ctx.user_id,
        )
        session.add(period)
        created.append(period)
    await session.flush()
    return sorted(created, key=lambda p: p.period_no)


async def list_periods(
    session: AsyncSession, ctx: AccessContext, *, filters: dict[str, Any]
) -> list[AccountingPeriod]:
    stmt = scope_filter(select(AccountingPeriod), AccountingPeriod, ctx, PERM_VIEW)
    if filters.get("fiscal_year") is not None:
        stmt = stmt.where(AccountingPeriod.fiscal_year == filters["fiscal_year"])
    rows = await session.execute(
        stmt.order_by(AccountingPeriod.fiscal_year, AccountingPeriod.period_no)
    )
    return list(rows.scalars().all())


async def get(session: AsyncSession, ctx: AccessContext, period_id: UUID) -> AccountingPeriod:
    stmt = scope_filter(select(AccountingPeriod), AccountingPeriod, ctx, PERM_VIEW).where(
        AccountingPeriod.id == period_id
    )
    row: AccountingPeriod | None = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Accounting period", period_id)
    return row


async def period_for_date(
    session: AsyncSession, *, company_id: UUID, on: date
) -> AccountingPeriod | None:
    """The period a date falls in, for resolving a journal entry's `entry_date`
    to a `period_id`. None when nobody has generated periods that far yet."""
    found: AccountingPeriod | None = await session.scalar(
        select(AccountingPeriod).where(
            AccountingPeriod.company_id == company_id,
            AccountingPeriod.start_date <= on,
            AccountingPeriod.end_date >= on,
        )
    )
    return found


def _human(status: str) -> str:
    return status.lower()


async def close(session: AsyncSession, ctx: AccessContext, period_id: UUID) -> AccountingPeriod:
    period = await get(session, ctx, period_id)
    if not ctx.has(PERM_MANAGE):
        raise PermissionDeniedError(PERM_MANAGE)
    if period.status != PeriodStatus.OPEN.value:
        raise BusinessRuleError(
            "period_not_open",
            f"Period {period.period_no} of FY{period.fiscal_year} is already "
            f"{_human(period.status)}.",
        )
    period.status = PeriodStatus.CLOSED.value
    period.closed_by_id = ctx.user_id
    period.closed_at = utcnow()
    period.version += 1
    await session.flush()
    return period


async def reopen(session: AsyncSession, ctx: AccessContext, period_id: UUID) -> AccountingPeriod:
    period = await get(session, ctx, period_id)
    if not ctx.has(PERM_MANAGE):
        raise PermissionDeniedError(PERM_MANAGE)
    if period.status == PeriodStatus.LOCKED.value:
        raise BusinessRuleError(
            "period_locked",
            f"Period {period.period_no} of FY{period.fiscal_year} is locked and cannot be "
            "reopened.",
        )
    if period.status != PeriodStatus.CLOSED.value:
        raise BusinessRuleError("period_not_closed", f"Period {period.period_no} is not closed.")
    period.status = PeriodStatus.OPEN.value
    period.closed_by_id = None
    period.closed_at = None
    period.version += 1
    await session.flush()
    return period


async def lock(session: AsyncSession, ctx: AccessContext, period_id: UUID) -> AccountingPeriod:
    period = await get(session, ctx, period_id)
    if not ctx.has(PERM_MANAGE):
        raise PermissionDeniedError(PERM_MANAGE)
    if period.status != PeriodStatus.CLOSED.value:
        raise BusinessRuleError(
            "period_not_closed",
            f"Period {period.period_no} of FY{period.fiscal_year} must be closed before it can "
            "be locked.",
        )
    period.status = PeriodStatus.LOCKED.value
    period.version += 1
    await session.flush()
    return period
