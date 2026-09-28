"""Finance endpoints: chart of accounts, periods, journal entries, reports
(docs/07 §`/finance`)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.access import AccessContext
from app.core.errors import NotFoundError
from app.core.pagination import Page
from app.core.scoping import assert_in_scope
from app.core.types import today_utc
from app.modules.finance import schemas as s
from app.modules.finance.domain.enums import BudgetStatus, JournalSourceType, JournalStatus
from app.modules.finance.models import Account, Budget, BudgetLine, JournalEntry, PostingRule
from app.modules.finance.services import (
    accounts,
    budgets,
    journal_entries,
    ledger,
    periods,
    posting_rules,
    reports,
)
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.vendors.services import vendor_lookup

router = APIRouter(prefix="/finance", tags=["finance"])

IfMatch = Annotated[int | None, Header(alias="If-Match", description="The version you loaded")]


# -----------------------------------------------------------------------------
# Accounts
# -----------------------------------------------------------------------------


def _account_view(account: Account) -> s.AccountRead:
    return s.AccountRead.model_validate(account)


@router.get(
    "/accounts", response_model=Page[s.AccountRead], dependencies=[require(accounts.PERM_VIEW)]
)
async def list_accounts(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    account_type: str | None = None,
    is_active: bool | None = None,
) -> Page[s.AccountRead]:
    rows, total = await accounts.list_accounts(
        session,
        ctx,
        page=page,
        search=q,
        filters={"account_type": account_type, "is_active": is_active},
    )
    return Page.of([_account_view(a) for a in rows], params=page, total=total)


@router.get(
    "/accounts/tree",
    response_model=list[s.AccountNodeRead],
    dependencies=[require(accounts.PERM_VIEW)],
    summary="The chart of accounts as a tree, for the CoA screen and account pickers",
)
async def accounts_tree(ctx: Access, session: SessionDep) -> list[s.AccountNodeRead]:
    rows = await accounts.all_active(session, ctx)

    def build(node: accounts.AccountNode) -> s.AccountNodeRead:
        return s.AccountNodeRead(
            account=_account_view(node.account), children=[build(c) for c in node.children]
        )

    return [build(n) for n in accounts.tree(rows)]


@router.post(
    "/accounts",
    response_model=s.AccountRead,
    status_code=201,
    dependencies=[require(accounts.PERM_MANAGE)],
)
async def create_account(payload: s.AccountCreate, ctx: Access, uow: UowDep) -> s.AccountRead:
    row = await accounts.create(
        uow.session,
        ctx,
        accounts.AccountInput(
            code=payload.code.strip(),
            name=payload.name,
            account_type=payload.account_type,
            parent_id=payload.parent_id,
            requires_project=payload.requires_project,
            requires_cost_center=payload.requires_cost_center,
            description=payload.description,
        ),
    )
    return _account_view(row)


@router.get(
    "/accounts/{account_id}",
    response_model=s.AccountRead,
    dependencies=[require(accounts.PERM_VIEW)],
)
async def get_account(account_id: UUID, ctx: Access, session: SessionDep) -> s.AccountRead:
    return _account_view(await accounts.get(session, ctx, account_id))


@router.patch(
    "/accounts/{account_id}",
    response_model=s.AccountRead,
    dependencies=[require(accounts.PERM_MANAGE)],
)
async def update_account(
    account_id: UUID, payload: s.AccountEdit, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.AccountRead:
    row = await accounts.update(
        uow.session,
        ctx,
        account_id,
        accounts.AccountEdit(
            name=payload.name,
            description=payload.description,
            requires_project=payload.requires_project,
            requires_cost_center=payload.requires_cost_center,
            is_active=payload.is_active,
        ),
        expected_version=if_match,
    )
    # The update just flushed, which expires server-computed columns
    # (updated_at); refresh before reading them back synchronously below.
    await uow.session.refresh(row)
    return _account_view(row)


# -----------------------------------------------------------------------------
# Periods
# -----------------------------------------------------------------------------


@router.get(
    "/periods", response_model=list[s.PeriodRead], dependencies=[require(periods.PERM_VIEW)]
)
async def list_periods(
    ctx: Access, session: SessionDep, fiscal_year: int | None = None
) -> list[s.PeriodRead]:
    rows = await periods.list_periods(session, ctx, filters={"fiscal_year": fiscal_year})
    return [s.PeriodRead.model_validate(p) for p in rows]


@router.post(
    "/periods/generate",
    response_model=list[s.PeriodRead],
    status_code=201,
    dependencies=[require(periods.PERM_MANAGE)],
    summary="Create the twelve periods of a fiscal year. Safe to call again for the same year.",
)
async def generate_periods(
    payload: s.GenerateFiscalYear, ctx: Access, uow: UowDep
) -> list[s.PeriodRead]:
    rows = await periods.generate_fiscal_year(uow.session, ctx, payload.fiscal_year)
    return [s.PeriodRead.model_validate(p) for p in rows]


@router.post(
    "/periods/{period_id}/close",
    response_model=s.PeriodRead,
    dependencies=[require(periods.PERM_MANAGE)],
)
async def close_period(period_id: UUID, ctx: Access, uow: UowDep) -> s.PeriodRead:
    return s.PeriodRead.model_validate(await periods.close(uow.session, ctx, period_id))


@router.post(
    "/periods/{period_id}/reopen",
    response_model=s.PeriodRead,
    dependencies=[require(periods.PERM_MANAGE)],
)
async def reopen_period(period_id: UUID, ctx: Access, uow: UowDep) -> s.PeriodRead:
    return s.PeriodRead.model_validate(await periods.reopen(uow.session, ctx, period_id))


@router.post(
    "/periods/{period_id}/lock",
    response_model=s.PeriodRead,
    dependencies=[require(periods.PERM_MANAGE)],
)
async def lock_period(period_id: UUID, ctx: Access, uow: UowDep) -> s.PeriodRead:
    return s.PeriodRead.model_validate(await periods.lock(uow.session, ctx, period_id))


# -----------------------------------------------------------------------------
# Journal entries
# -----------------------------------------------------------------------------


def _lines_input(payload: s.JournalEntryCreate) -> list[ledger.LineInput]:
    return [
        ledger.LineInput(
            account_id=line.account_id,
            debit=line.debit,
            credit=line.credit,
            description=line.description,
            project_id=line.project_id,
            phase_id=line.phase_id,
            site_id=line.site_id,
            department_id=line.department_id,
            cost_center_id=line.cost_center_id,
            vendor_id=line.vendor_id,
            customer_id=line.customer_id,
            employee_id=line.employee_id,
            material_id=line.material_id,
        )
        for line in payload.lines
    ]


def _may(ctx: AccessContext, permission: str, entry: JournalEntry) -> bool:
    try:
        assert_in_scope(ctx, permission, company_id=entry.company_id, entity="Journal entry")
    except Exception:  # noqa: BLE001 - any refusal simply means "no"
        return False
    return True


async def _detail(
    session: AsyncSession, ctx: AccessContext, entry: JournalEntry
) -> s.JournalEntryRead:
    # A submit/approve/reverse just flushed inside a nested transaction; server-
    # computed columns like updated_at are expired until refreshed, and reading
    # an expired column from a plain (non-awaited) attribute access is exactly
    # the MissingGreenlet trap.
    await session.refresh(entry)
    await session.refresh(entry, attribute_names=["lines"])
    company = ctx.company_id
    account_ids = {line.account_id for line in entry.lines}
    accounts_by_id = {
        a.id: a
        for a in (await session.execute(select(Account).where(Account.id.in_(account_ids))))
        .scalars()
        .all()
    }
    codes = await document_lookup.codes(
        session,
        company_id=company,
        project_ids={line.project_id for line in entry.lines if line.project_id},
        site_ids={line.site_id for line in entry.lines if line.site_id},
        cost_center_ids={line.cost_center_id for line in entry.lines if line.cost_center_id},
    )
    vendors = await vendor_lookup.vendors(
        session,
        company_id=company,
        vendor_ids={line.vendor_id for line in entry.lines if line.vendor_id},
    )
    materials = await material_lookup.materials(
        session,
        company_id=company,
        material_ids={line.material_id for line in entry.lines if line.material_id},
    )
    period = await periods.get(session, ctx, entry.period_id)

    lines = []
    for line in entry.lines:
        account = accounts_by_id.get(line.account_id)
        lines.append(
            s.JournalLineRead(
                id=line.id,
                line_no=line.line_no,
                account_id=line.account_id,
                account_code=account.code if account else None,
                account_name=account.name if account else None,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
                project_id=line.project_id,
                project_code=codes.get(line.project_id, (None, None))[0]
                if line.project_id
                else None,
                phase_id=line.phase_id,
                site_id=line.site_id,
                site_code=codes.get(line.site_id, (None, None))[0] if line.site_id else None,
                department_id=line.department_id,
                cost_center_id=line.cost_center_id,
                cost_center_code=(
                    codes.get(line.cost_center_id, (None, None))[0] if line.cost_center_id else None
                ),
                vendor_id=line.vendor_id,
                vendor_name=vendors[line.vendor_id].name if line.vendor_id in vendors else None,
                customer_id=line.customer_id,
                employee_id=line.employee_id,
                material_id=line.material_id,
                material_name=materials[line.material_id].name
                if line.material_id in materials
                else None,
            )
        )

    view = s.JournalEntryRead.model_validate(entry)
    view.lines = lines
    view.fiscal_year = period.fiscal_year
    view.period_no = period.period_no
    status = JournalStatus(entry.status)
    can_edit = status.is_editable and _may(ctx, journal_entries.PERM_CREATE, entry)
    view.can_edit = can_edit
    view.can_submit = can_edit
    view.can_delete = can_edit
    view.can_withdraw = (
        status is JournalStatus.PENDING_APPROVAL
        and entry.updated_by_id == ctx.user_id
        and _may(ctx, journal_entries.PERM_CREATE, entry)
    )
    view.can_reverse = status is JournalStatus.POSTED and _may(
        ctx, journal_entries.PERM_REVERSE, entry
    )
    return view


@router.get(
    "/journal-entries",
    response_model=Page[s.JournalEntryListItem],
    dependencies=[require(journal_entries.PERM_VIEW)],
)
async def list_journal_entries(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    source_type: str | None = None,
) -> Page[s.JournalEntryListItem]:
    rows, total = await journal_entries.list_entries(
        session, ctx, page=page, search=q, filters={"status": status, "source_type": source_type}
    )
    return Page.of(
        [s.JournalEntryListItem.model_validate(e) for e in rows], params=page, total=total
    )


@router.post(
    "/journal-entries",
    response_model=s.JournalEntryRead,
    status_code=201,
    dependencies=[require(journal_entries.PERM_CREATE)],
    summary="Draft a manual journal entry. It moves nothing until it is approved.",
)
async def create_journal_entry(
    payload: s.JournalEntryCreate, ctx: Access, uow: UowDep
) -> s.JournalEntryRead:
    entry = await journal_entries.create(
        uow.session,
        ctx,
        journal_entries.JournalEntryInput(
            entry_date=payload.entry_date,
            description=payload.description,
            reference=payload.reference,
            lines=_lines_input(payload),
        ),
    )
    return await _detail(uow.session, ctx, entry)


@router.get(
    "/journal-entries/{je_id}",
    response_model=s.JournalEntryRead,
    dependencies=[require(journal_entries.PERM_VIEW)],
)
async def get_journal_entry(je_id: UUID, ctx: Access, session: SessionDep) -> s.JournalEntryRead:
    return await _detail(session, ctx, await journal_entries.get(session, ctx, je_id))


@router.put(
    "/journal-entries/{je_id}",
    response_model=s.JournalEntryRead,
    dependencies=[require(journal_entries.PERM_CREATE)],
    summary="Edit a draft (or a rejected entry, which is a draft again)",
)
async def update_journal_entry(
    je_id: UUID, payload: s.JournalEntryCreate, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.JournalEntryRead:
    entry = await journal_entries.update(
        uow.session,
        ctx,
        je_id,
        journal_entries.JournalEntryInput(
            entry_date=payload.entry_date,
            description=payload.description,
            reference=payload.reference,
            lines=_lines_input(payload),
        ),
        expected_version=if_match,
    )
    return await _detail(uow.session, ctx, entry)


@router.delete(
    "/journal-entries/{je_id}",
    status_code=204,
    dependencies=[require(journal_entries.PERM_CREATE)],
    summary="Delete a draft that never happened: there is nothing yet for the ledger to protect",
)
async def delete_journal_entry(je_id: UUID, ctx: Access, uow: UowDep) -> None:
    await journal_entries.delete_draft(uow.session, ctx, je_id)


@router.post(
    "/journal-entries/{je_id}/submit",
    response_model=s.JournalEntryRead,
    dependencies=[require(journal_entries.PERM_CREATE)],
)
async def submit_journal_entry(
    je_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.JournalEntryRead:
    entry = await journal_entries.submit(uow.session, ctx, je_id, expected_version=if_match)
    return await _detail(uow.session, ctx, entry)


@router.post(
    "/journal-entries/{je_id}/withdraw",
    response_model=s.JournalEntryRead,
    dependencies=[require(journal_entries.PERM_CREATE)],
)
async def withdraw_journal_entry(
    je_id: UUID, ctx: Access, uow: UowDep, payload: s.CancelBody | None = None
) -> s.JournalEntryRead:
    entry = await journal_entries.withdraw(
        uow.session, ctx, je_id, payload.reason if payload else None
    )
    return await _detail(uow.session, ctx, entry)


@router.post(
    "/journal-entries/{je_id}/reverse",
    response_model=s.JournalEntryRead,
    status_code=201,
    dependencies=[require(journal_entries.PERM_REVERSE)],
    summary="Post a new entry with every line reversed. The original is marked REVERSED.",
)
async def reverse_journal_entry(
    je_id: UUID, payload: s.CancelBody, ctx: Access, uow: UowDep
) -> s.JournalEntryRead:
    reversal = await journal_entries.reverse(uow.session, ctx, je_id, payload.reason)
    return await _detail(uow.session, ctx, reversal)


# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------


@router.get(
    "/trial-balance",
    response_model=s.TrialBalanceRead,
    dependencies=[require(journal_entries.PERM_VIEW)],
)
async def get_trial_balance(
    ctx: Access, session: SessionDep, as_of: date | None = None
) -> s.TrialBalanceRead:
    rows = await reports.trial_balance(session, ctx, as_of=as_of)
    return s.TrialBalanceRead(
        as_of=as_of or today_utc(),
        rows=[s.TrialBalanceRowRead.model_validate(r) for r in rows],
        total_debit=sum((r.debit for r in rows), start=Decimal(0)),
        total_credit=sum((r.credit for r in rows), start=Decimal(0)),
    )


@router.get(
    "/general-ledger",
    response_model=s.GeneralLedgerRead,
    dependencies=[require(journal_entries.PERM_VIEW)],
)
async def get_general_ledger(
    account_id: UUID,
    ctx: Access,
    session: SessionDep,
    from_date: date | None = None,
    to_date: date | None = None,
) -> s.GeneralLedgerRead:
    result = await reports.general_ledger(
        session, ctx, account_id=account_id, from_date=from_date, to_date=to_date
    )
    if result is None:
        raise NotFoundError("Account", account_id)
    return s.GeneralLedgerRead(
        account_id=result.account.id,
        account_code=result.account.code,
        account_name=result.account.name,
        opening_balance=result.opening_balance,
        rows=[s.GeneralLedgerRowRead.model_validate(r) for r in result.rows],
        closing_balance=result.closing_balance,
    )


# -----------------------------------------------------------------------------
# Posting rules
# -----------------------------------------------------------------------------


async def _posting_rule_views(
    session: AsyncSession, rows: list[PostingRule]
) -> list[s.PostingRuleRead]:
    account_ids = {r.debit_account_id for r in rows} | {r.credit_account_id for r in rows}
    codes = {
        a.id: a.code
        for a in (await session.execute(select(Account).where(Account.id.in_(account_ids))))
        .scalars()
        .all()
    }
    out = []
    for row in rows:
        view = s.PostingRuleRead.model_validate(row)
        view.debit_account_code = codes.get(row.debit_account_id)
        view.credit_account_code = codes.get(row.credit_account_id)
        out.append(view)
    return out


@router.get(
    "/posting-rules",
    response_model=Page[s.PostingRuleRead],
    dependencies=[require(posting_rules.PERM_VIEW)],
)
async def list_posting_rules(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    source_type: JournalSourceType | None = None,
    event: str | None = None,
) -> Page[s.PostingRuleRead]:
    rows, total = await posting_rules.list_rules(
        session,
        ctx,
        page=page,
        search=None,
        filters={"source_type": source_type.value if source_type else None, "event": event},
    )
    return Page.of(await _posting_rule_views(session, rows), params=page, total=total)


@router.post(
    "/posting-rules",
    response_model=s.PostingRuleRead,
    status_code=201,
    dependencies=[require(posting_rules.PERM_MANAGE)],
)
async def create_posting_rule(
    payload: s.PostingRuleCreate, ctx: Access, uow: UowDep
) -> s.PostingRuleRead:
    row = await posting_rules.create(
        uow.session,
        ctx,
        posting_rules.PostingRuleInput(
            source_type=payload.source_type,
            event=payload.event,
            debit_account_id=payload.debit_account_id,
            credit_account_id=payload.credit_account_id,
            name=payload.name,
            condition=payload.condition,
            priority=payload.priority,
            is_active=payload.is_active,
        ),
    )
    return (await _posting_rule_views(uow.session, [row]))[0]


@router.patch(
    "/posting-rules/{rule_id}",
    response_model=s.PostingRuleRead,
    dependencies=[require(posting_rules.PERM_MANAGE)],
)
async def update_posting_rule(
    rule_id: UUID, payload: s.PostingRuleEdit, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.PostingRuleRead:
    row = await posting_rules.update(
        uow.session,
        ctx,
        rule_id,
        posting_rules.PostingRuleEdit(
            name=payload.name,
            condition=payload.condition,
            debit_account_id=payload.debit_account_id,
            credit_account_id=payload.credit_account_id,
            priority=payload.priority,
            is_active=payload.is_active,
        ),
        expected_version=if_match,
    )
    await uow.session.refresh(row)
    return (await _posting_rule_views(uow.session, [row]))[0]


# -----------------------------------------------------------------------------
# Budgets
# -----------------------------------------------------------------------------


async def _budget_line_views(
    session: AsyncSession, company_id: UUID, lines: list[BudgetLine]
) -> list[s.BudgetLineRead]:
    account_ids = {line.account_id for line in lines}
    account_codes = {
        a.id: (a.code, a.name)
        for a in (await session.execute(select(Account).where(Account.id.in_(account_ids))))
        .scalars()
        .all()
    }
    codes = await document_lookup.codes(
        session,
        company_id=company_id,
        project_ids=set(),
        phase_ids={line.phase_id for line in lines if line.phase_id},
        cost_center_ids={line.cost_center_id for line in lines if line.cost_center_id},
    )
    out = []
    for line in lines:
        effective = line.revised_amount if line.revised_amount is not None else line.budgeted_amount
        view = s.BudgetLineRead.model_validate(line)
        account = account_codes.get(line.account_id)
        view.account_code = account[0] if account else None
        view.account_name = account[1] if account else None
        view.phase_code = codes.get(line.phase_id, (None, None))[0] if line.phase_id else None
        view.cost_center_code = (
            codes.get(line.cost_center_id, (None, None))[0] if line.cost_center_id else None
        )
        view.remaining_amount = effective - line.committed_amount - line.actual_amount
        view.variance_pct = (
            ((line.actual_amount - effective) / effective * 100) if effective else None
        )
        out.append(view)
    return out


async def _budget_detail(session: AsyncSession, ctx: AccessContext, budget: Budget) -> s.BudgetRead:
    project_codes = await document_lookup.codes(
        session, company_id=ctx.company_id, project_ids={budget.project_id}
    )
    view = s.BudgetRead.model_validate(budget)
    view.project_code = project_codes.get(budget.project_id, (None, None))[0]
    view.lines = await _budget_line_views(session, ctx.company_id, list(budget.lines))
    status = BudgetStatus(budget.status)
    can_approve_step = status in (BudgetStatus.APPROVED, BudgetStatus.REVISED) and ctx.has(
        budgets.PERM_APPROVE
    )
    view.can_edit = status is BudgetStatus.DRAFT and ctx.has(budgets.PERM_CREATE)
    view.can_approve = status is BudgetStatus.DRAFT and ctx.has(budgets.PERM_APPROVE)
    view.can_revise = can_approve_step
    view.can_close = can_approve_step
    return view


@router.get(
    "/budgets", response_model=Page[s.BudgetListItem], dependencies=[require(budgets.PERM_VIEW)]
)
async def list_budgets(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    project_id: UUID | None = None,
    fiscal_year: int | None = None,
) -> Page[s.BudgetListItem]:
    rows, total = await budgets.list_budgets(
        session, ctx, page=page, filters={"project_id": project_id, "fiscal_year": fiscal_year}
    )
    project_codes = await document_lookup.codes(
        session, company_id=ctx.company_id, project_ids={r.project_id for r in rows}
    )
    items = []
    for row in rows:
        item = s.BudgetListItem.model_validate(row)
        item.project_code = project_codes.get(row.project_id, (None, None))[0]
        items.append(item)
    return Page.of(items, params=page, total=total)


@router.post(
    "/budgets",
    response_model=s.BudgetRead,
    status_code=201,
    dependencies=[require(budgets.PERM_CREATE)],
)
async def create_budget(payload: s.BudgetCreate, ctx: Access, uow: UowDep) -> s.BudgetRead:
    budget = await budgets.create(
        uow.session,
        ctx,
        budgets.BudgetInput(
            project_id=payload.project_id,
            fiscal_year=payload.fiscal_year,
            name=payload.name,
            lines=[
                budgets.BudgetLineInput(
                    account_id=line.account_id,
                    budgeted_amount=line.budgeted_amount,
                    phase_id=line.phase_id,
                    cost_center_id=line.cost_center_id,
                    material_category_id=line.material_category_id,
                    period_id=line.period_id,
                )
                for line in payload.lines
            ],
        ),
    )
    return await _budget_detail(uow.session, ctx, budget)


@router.get(
    "/budgets/{budget_id}", response_model=s.BudgetRead, dependencies=[require(budgets.PERM_VIEW)]
)
async def get_budget(budget_id: UUID, ctx: Access, session: SessionDep) -> s.BudgetRead:
    return await _budget_detail(session, ctx, await budgets.get(session, ctx, budget_id))


@router.get(
    "/budgets/{budget_id}/vs-actual",
    response_model=s.BudgetVsActualRead,
    dependencies=[require(budgets.PERM_VIEW)],
)
async def get_budget_vs_actual(
    budget_id: UUID, ctx: Access, session: SessionDep
) -> s.BudgetVsActualRead:
    budget = await budgets.get(session, ctx, budget_id)
    project_codes = await document_lookup.codes(
        session, company_id=ctx.company_id, project_ids={budget.project_id}
    )
    lines = await _budget_line_views(session, ctx.company_id, list(budget.lines))
    return s.BudgetVsActualRead(
        id=budget.id,
        project_id=budget.project_id,
        project_code=project_codes.get(budget.project_id, (None, None))[0],
        fiscal_year=budget.fiscal_year,
        name=budget.name,
        status=budget.status,
        lines=lines,
        total_budgeted=sum((line.budgeted_amount for line in lines), Decimal(0)),
        total_committed=sum((line.committed_amount for line in lines), Decimal(0)),
        total_actual=sum((line.actual_amount for line in lines), Decimal(0)),
        total_remaining=sum((line.remaining_amount for line in lines), Decimal(0)),
    )


@router.put(
    "/budgets/{budget_id}", response_model=s.BudgetRead, dependencies=[require(budgets.PERM_CREATE)]
)
async def update_budget(
    budget_id: UUID, payload: s.BudgetCreate, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.BudgetRead:
    budget = await budgets.update(
        uow.session,
        ctx,
        budget_id,
        budgets.BudgetInput(
            project_id=payload.project_id,
            fiscal_year=payload.fiscal_year,
            name=payload.name,
            lines=[
                budgets.BudgetLineInput(
                    account_id=line.account_id,
                    budgeted_amount=line.budgeted_amount,
                    phase_id=line.phase_id,
                    cost_center_id=line.cost_center_id,
                    material_category_id=line.material_category_id,
                    period_id=line.period_id,
                )
                for line in payload.lines
            ],
        ),
        expected_version=if_match,
    )
    return await _budget_detail(uow.session, ctx, budget)


@router.post(
    "/budgets/{budget_id}/approve",
    response_model=s.BudgetRead,
    dependencies=[require(budgets.PERM_APPROVE)],
)
async def approve_budget(
    budget_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.BudgetRead:
    budget = await budgets.approve(uow.session, ctx, budget_id, expected_version=if_match)
    return await _budget_detail(uow.session, ctx, budget)


@router.post(
    "/budgets/{budget_id}/revise",
    response_model=s.BudgetRead,
    dependencies=[require(budgets.PERM_APPROVE)],
    summary="Set a new revised_amount on one or more of the budget's existing lines",
)
async def revise_budget(
    budget_id: UUID, payload: s.BudgetRevise, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.BudgetRead:
    budget = await budgets.revise_lines(
        uow.session, ctx, budget_id, payload.revisions, expected_version=if_match
    )
    return await _budget_detail(uow.session, ctx, budget)


@router.post(
    "/budgets/{budget_id}/close",
    response_model=s.BudgetRead,
    dependencies=[require(budgets.PERM_APPROVE)],
)
async def close_budget(
    budget_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.BudgetRead:
    budget = await budgets.close(uow.session, ctx, budget_id, expected_version=if_match)
    return await _budget_detail(uow.session, ctx, budget)


@router.get(
    "/commitments",
    response_model=Page[s.BudgetCommitmentRead],
    dependencies=[require(budgets.PERM_VIEW)],
)
async def list_commitments(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    budget_line_id: UUID | None = None,
    source_type: str | None = None,
    source_id: UUID | None = None,
) -> Page[s.BudgetCommitmentRead]:
    rows, total = await budgets.list_commitments(
        session,
        ctx,
        page=page,
        filters={
            "budget_line_id": budget_line_id,
            "source_type": source_type,
            "source_id": source_id,
        },
    )
    return Page.of(
        [s.BudgetCommitmentRead.model_validate(r) for r in rows], params=page, total=total
    )
