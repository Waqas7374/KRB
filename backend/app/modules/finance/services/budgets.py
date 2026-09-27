"""Budgets, their lines, and the commitments a purchase order and a goods
receipt write against them (docs/02 §8, docs/07 §3).

Unlike a manual journal entry, approving a budget is a single permission check
(`finance.budget.approve`), not a routed, multi-step workflow — docs/07 §2
gives it its own `POST /finance/budgets/{id}/approve`, not the shared
`/approvals` endpoint. `REVISED` is not a step someone chooses either: it is
entered the moment a line's `revised_amount` is first set on an approved
budget, and stays until the budget closes.

`commit_purchase_order` and `release_receipt` are the two ends of *committed →
actual → remaining*: an approved PO's items resolve, through the same posting
rule a GRN receipt will use, to a debit account; whichever budget line matches
that account under the PO's own phase and cost centre is committed. Neither
function is a gate — a PO or a GRN with nothing to match (no budget for that
project and year, or no line for that phase/cost centre/account) posts and
commits normally regardless. A budget is something to measure against, not
something that blocks spending it has not been told about.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from app.core.access import AccessContext
from app.core.errors import (
    BusinessRuleError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.pagination import PageParams
from app.core.scoping import scope_filter
from app.core.types import utcnow
from app.modules.finance.domain.enums import BudgetStatus, CommitmentSourceType, CommitmentStatus
from app.modules.finance.models import Budget, BudgetCommitment, BudgetLine
from app.modules.finance.services import periods, posting_rules

PERM_VIEW = "finance.budget.view"
PERM_CREATE = "finance.budget.create"
PERM_APPROVE = "finance.budget.approve"


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


def _eq(column: InstrumentedAttribute[UUID | None], value: UUID | None) -> ColumnElement[bool]:
    """`col IS NULL` when the dimension is not set, `col = value` otherwise —
    plain `==` never matches a NULL column against a NULL parameter in SQL."""
    return column.is_(None) if value is None else column == value


# -----------------------------------------------------------------------------
# Budget / budget lines
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetLineInput:
    account_id: UUID
    budgeted_amount: Decimal
    phase_id: UUID | None = None
    cost_center_id: UUID | None = None
    material_category_id: UUID | None = None
    period_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class BudgetInput:
    project_id: UUID
    fiscal_year: int
    name: str
    lines: list[BudgetLineInput]


def _validate_lines(lines: list[BudgetLineInput]) -> None:
    if not lines:
        raise BusinessRuleError("budget_empty", "A budget needs at least one line.")
    seen: set[tuple[UUID | None, UUID | None, UUID]] = set()
    errors: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if line.budgeted_amount < 0:
            errors.append(
                {
                    "field": f"lines.{index}.budgeted_amount",
                    "code": "invalid",
                    "message": "Cannot be negative",
                }
            )
        key = (line.phase_id, line.cost_center_id, line.account_id)
        if key in seen:
            errors.append(
                {
                    "field": f"lines.{index}.account_id",
                    "code": "duplicate",
                    "message": "This phase, cost centre and account are already a line above",
                }
            )
        seen.add(key)
    if errors:
        raise ValidationError("The budget is not valid.", errors=errors)


def _line_rows(lines: list[BudgetLineInput]) -> list[BudgetLine]:
    return [
        BudgetLine(
            phase_id=line.phase_id,
            cost_center_id=line.cost_center_id,
            account_id=line.account_id,
            material_category_id=line.material_category_id,
            period_id=line.period_id,
            budgeted_amount=line.budgeted_amount,
        )
        for line in lines
    ]


def _retotal(budget: Budget) -> None:
    budget.total_amount = sum(
        (line.revised_amount if line.revised_amount is not None else line.budgeted_amount)
        for line in budget.lines
    ) or Decimal(0)


async def create(session: AsyncSession, ctx: AccessContext, data: BudgetInput) -> Budget:
    if not ctx.has(PERM_CREATE):
        raise PermissionDeniedError(PERM_CREATE)
    _validate_lines(data.lines)
    existing = await session.scalar(
        select(Budget.id).where(
            Budget.company_id == ctx.company_id,
            Budget.project_id == data.project_id,
            Budget.fiscal_year == data.fiscal_year,
        )
    )
    if existing is not None:
        raise BusinessRuleError(
            "budget_exists", f"A budget for this project and FY{data.fiscal_year} already exists."
        )
    budget = Budget(
        company_id=ctx.company_id,
        project_id=data.project_id,
        fiscal_year=data.fiscal_year,
        name=data.name.strip(),
        status=BudgetStatus.DRAFT.value,
        lines=_line_rows(data.lines),
        created_by_id=ctx.user_id,
    )
    _retotal(budget)
    session.add(budget)
    await session.flush()
    return budget


def _check_version(budget: Budget, expected: int | None) -> None:
    if expected is not None and budget.version != expected:
        raise VersionConflictError(
            f"{budget.name} was changed by someone else (version {budget.version}, you had "
            f"{expected}). Reload and try again."
        )


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    budget_id: UUID,
    data: BudgetInput,
    *,
    expected_version: int | None,
) -> Budget:
    if not ctx.has(PERM_CREATE):
        raise PermissionDeniedError(PERM_CREATE)
    budget = await get(session, ctx, budget_id)
    _check_version(budget, expected_version)
    if BudgetStatus(budget.status) is not BudgetStatus.DRAFT:
        raise BusinessRuleError(
            "budget_not_draft",
            f"{budget.name} is {budget.status.lower()}; only a draft can be edited.",
        )
    _validate_lines(data.lines)
    budget.name = data.name.strip()
    budget.lines.clear()
    await session.flush()
    budget.lines.extend(_line_rows(data.lines))
    _retotal(budget)
    budget.updated_by_id = ctx.user_id
    budget.version += 1
    await session.flush()
    return budget


async def approve(
    session: AsyncSession, ctx: AccessContext, budget_id: UUID, *, expected_version: int | None
) -> Budget:
    if not ctx.has(PERM_APPROVE):
        raise PermissionDeniedError(PERM_APPROVE)
    budget = await get(session, ctx, budget_id)
    _check_version(budget, expected_version)
    if BudgetStatus(budget.status) is not BudgetStatus.DRAFT:
        raise BusinessRuleError(
            "budget_not_draft",
            f"{budget.name} is {budget.status.lower()}; only a draft can be approved.",
        )
    budget.status = BudgetStatus.APPROVED.value
    budget.approved_at = utcnow()
    budget.approved_by_id = ctx.user_id
    budget.version += 1
    await session.flush()
    return budget


async def revise_lines(
    session: AsyncSession,
    ctx: AccessContext,
    budget_id: UUID,
    revisions: dict[UUID, Decimal],
    *,
    expected_version: int | None,
) -> Budget:
    """Set `revised_amount` on some of an approved budget's existing lines.
    Lines are neither added nor removed by a revision — that would change what
    the budget covers, not just how much of it there is."""
    if not ctx.has(PERM_APPROVE):
        raise PermissionDeniedError(PERM_APPROVE)
    budget = await get(session, ctx, budget_id)
    _check_version(budget, expected_version)
    if BudgetStatus(budget.status) not in (BudgetStatus.APPROVED, BudgetStatus.REVISED):
        raise BusinessRuleError(
            "budget_not_approved",
            f"{budget.name} is {budget.status.lower()}; it cannot be revised.",
        )
    by_id = {line.id: line for line in budget.lines}
    errors = [
        {"field": f"revisions.{line_id}", "code": "invalid", "message": "Unknown budget line"}
        for line_id in revisions
        if line_id not in by_id
    ]
    if errors:
        raise ValidationError("Some lines are not on this budget.", errors=errors)
    for line_id, amount in revisions.items():
        if amount < 0:
            raise _fail(f"revisions.{line_id}", "Cannot be negative")
        by_id[line_id].revised_amount = amount
    budget.status = BudgetStatus.REVISED.value
    _retotal(budget)
    budget.updated_by_id = ctx.user_id
    budget.version += 1
    await session.flush()
    return budget


async def close(
    session: AsyncSession, ctx: AccessContext, budget_id: UUID, *, expected_version: int | None
) -> Budget:
    if not ctx.has(PERM_APPROVE):
        raise PermissionDeniedError(PERM_APPROVE)
    budget = await get(session, ctx, budget_id)
    _check_version(budget, expected_version)
    if BudgetStatus(budget.status) not in (BudgetStatus.APPROVED, BudgetStatus.REVISED):
        raise BusinessRuleError(
            "budget_not_approved",
            f"{budget.name} is {budget.status.lower()}; only an approved (or revised) budget can "
            "be closed.",
        )
    budget.status = BudgetStatus.CLOSED.value
    budget.closed_at = utcnow()
    budget.version += 1
    await session.flush()
    return budget


async def list_budgets(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    filters: dict[str, object],
) -> tuple[list[Budget], int]:
    if not ctx.has(PERM_VIEW):
        return [], 0
    stmt = scope_filter(select(Budget), Budget, ctx, PERM_VIEW)
    if filters.get("project_id") is not None:
        stmt = stmt.where(Budget.project_id == filters["project_id"])
    if filters.get("fiscal_year") is not None:
        stmt = stmt.where(Budget.fiscal_year == filters["fiscal_year"])
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.execute(
        stmt.order_by(Budget.fiscal_year.desc()).offset(page.offset).limit(page.limit)
    )
    return list(rows.scalars().all()), total or 0


async def get(session: AsyncSession, ctx: AccessContext, budget_id: UUID) -> Budget:
    stmt = scope_filter(select(Budget), Budget, ctx, PERM_VIEW).where(Budget.id == budget_id)
    budget: Budget | None = (await session.execute(stmt)).scalar_one_or_none()
    if budget is None:
        raise NotFoundError("Budget", budget_id)
    await session.refresh(budget, attribute_names=["lines"])
    return budget


# -----------------------------------------------------------------------------
# Commitments — written from procurement (PO approval) and GRN (posting)
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommitmentLine:
    material_id: UUID
    amount: Decimal
    # Per line, not per call: a goods receipt may mix a purchase order's own
    # lines with an ad-hoc extra one, and each must resolve its own account.
    is_po_backed: bool = True


async def _group_by_account(
    session: AsyncSession, ctx: AccessContext, lines: list[CommitmentLine]
) -> dict[tuple[UUID, bool], Decimal]:
    """Grouped for budget-tracking purposes only, by account *and* whether the
    line was PO-backed (the two never merge — a commitment is only ever
    released by a PO-backed line). Unlike the same resolution for an actual GL
    posting (`grn_service.post`, which must fail loudly if it cannot find an
    account), a line with no posting rule yet configured simply is not tracked
    against any budget — a budget is something to measure spend against, not a
    gate that blocks it."""
    totals: dict[tuple[UUID, bool], Decimal] = defaultdict(Decimal)
    for line in lines:
        if line.amount == 0:
            continue
        try:
            rule = await posting_rules.resolve_receipt_account(
                session, ctx, material_id=line.material_id, is_po_backed=line.is_po_backed
            )
        except BusinessRuleError:
            continue
        totals[(rule.debit_account_id, line.is_po_backed)] += line.amount
    return dict(totals)


async def _find_budget_line(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    project_id: UUID,
    on: date,
    phase_id: UUID | None,
    cost_center_id: UUID | None,
    account_id: UUID,
) -> BudgetLine | None:
    period = await periods.period_for_date(session, company_id=ctx.company_id, on=on)
    if period is None:
        return None
    budget = await session.scalar(
        select(Budget).where(
            Budget.company_id == ctx.company_id,
            Budget.project_id == project_id,
            Budget.fiscal_year == period.fiscal_year,
            Budget.status.in_((BudgetStatus.APPROVED.value, BudgetStatus.REVISED.value)),
        )
    )
    if budget is None:
        return None
    line: BudgetLine | None = await session.scalar(
        select(BudgetLine).where(
            BudgetLine.budget_id == budget.id,
            _eq(BudgetLine.phase_id, phase_id),
            _eq(BudgetLine.cost_center_id, cost_center_id),
            BudgetLine.account_id == account_id,
        )
    )
    return line


async def list_commitments(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    filters: dict[str, object],
) -> tuple[list[BudgetCommitment], int]:
    if not ctx.has(PERM_VIEW):
        return [], 0
    stmt = (
        select(BudgetCommitment)
        .join(BudgetLine)
        .join(Budget)
        .where(Budget.company_id == ctx.company_id)
    )
    if filters.get("budget_line_id") is not None:
        stmt = stmt.where(BudgetCommitment.budget_line_id == filters["budget_line_id"])
    if filters.get("source_type") is not None:
        stmt = stmt.where(BudgetCommitment.source_type == filters["source_type"])
    if filters.get("source_id") is not None:
        stmt = stmt.where(BudgetCommitment.source_id == filters["source_id"])
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.execute(
        stmt.order_by(BudgetCommitment.created_at.desc()).offset(page.offset).limit(page.limit)
    )
    return list(rows.scalars().all()), total or 0


async def commit_purchase_order(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    source_id: UUID,
    project_id: UUID,
    phase_id: UUID | None,
    cost_center_id: UUID | None,
    on: date,
    lines: list[CommitmentLine],
) -> None:
    """Called the instant a purchase order is approved. A PO's items resolve
    (via the GRN receipt posting rule, since that is the account the spend
    will eventually land in) to one or more debit accounts; each that matches
    a line of an approved budget for this project and fiscal year gets a new,
    OPEN commitment."""
    grouped = await _group_by_account(session, ctx, lines)
    for (account_id, _is_po_backed), amount in grouped.items():
        line = await _find_budget_line(
            session,
            ctx,
            project_id=project_id,
            on=on,
            phase_id=phase_id,
            cost_center_id=cost_center_id,
            account_id=account_id,
        )
        if line is None:
            continue
        session.add(
            BudgetCommitment(
                budget_line_id=line.id,
                source_type=CommitmentSourceType.PO.value,
                source_id=source_id,
                amount=amount,
                created_by_id=ctx.user_id,
            )
        )
        line.committed_amount += amount
    await session.flush()


async def release_receipt(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    source_type: CommitmentSourceType,
    source_id: UUID | None,
    project_id: UUID,
    phase_id: UUID | None,
    cost_center_id: UUID | None,
    on: date,
    lines: list[CommitmentLine],
) -> None:
    """Called the instant a GRN posts. Books the actual regardless of whether
    anything was committed (a counter purchase never was); when it was — a PO
    line received — the matching OPEN commitment is released by the same
    amount, capped at what remains of it."""
    grouped = await _group_by_account(session, ctx, lines)
    for (account_id, is_po_backed), amount in grouped.items():
        line = await _find_budget_line(
            session,
            ctx,
            project_id=project_id,
            on=on,
            phase_id=phase_id,
            cost_center_id=cost_center_id,
            account_id=account_id,
        )
        if line is None:
            continue
        line.actual_amount += amount
        if is_po_backed and source_id is not None:
            commitment = await session.scalar(
                select(BudgetCommitment).where(
                    BudgetCommitment.budget_line_id == line.id,
                    BudgetCommitment.source_type == source_type.value,
                    BudgetCommitment.source_id == source_id,
                    BudgetCommitment.status.in_(
                        (CommitmentStatus.OPEN.value, CommitmentStatus.PARTIALLY_RELEASED.value)
                    ),
                )
            )
            if commitment is not None:
                release = min(amount, commitment.amount - commitment.released_amount)
                commitment.released_amount += release
                # The committed figure only ever holds what is *still* open —
                # the moment it is released it is an actual, not a commitment.
                line.committed_amount -= release
                commitment.status = (
                    CommitmentStatus.RELEASED.value
                    if commitment.released_amount >= commitment.amount
                    else CommitmentStatus.PARTIALLY_RELEASED.value
                )
    await session.flush()
