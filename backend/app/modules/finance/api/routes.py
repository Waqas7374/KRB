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
from app.modules.finance.domain.enums import (
    BudgetStatus,
    JournalSourceType,
    JournalStatus,
    PaymentRequestStatus,
    PaymentStatus,
    VendorInvoiceStatus,
)
from app.modules.finance.models import (
    Account,
    BankAccount,
    Budget,
    BudgetLine,
    JournalEntry,
    Payment,
    PaymentRequest,
    PostingRule,
    VendorInvoice,
    VendorInvoiceMatch,
)
from app.modules.finance.services import (
    accounts,
    bank_accounts,
    budgets,
    journal_entries,
    ledger,
    payment_requests,
    payments,
    periods,
    posting_rules,
    reports,
    tax_codes,
    vendor_invoices,
)
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.procurement.services import po_lookup
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


# -----------------------------------------------------------------------------
# Tax codes
# -----------------------------------------------------------------------------


@router.get(
    "/tax-codes",
    response_model=Page[s.TaxCodeRead],
    dependencies=[require(tax_codes.PERM_VIEW)],
)
async def list_tax_codes(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    is_active: bool | None = None,
) -> Page[s.TaxCodeRead]:
    rows, total = await tax_codes.list_tax_codes(
        session, ctx, page=page, search=q, filters={"is_active": is_active}
    )
    return Page.of([s.TaxCodeRead.model_validate(r) for r in rows], params=page, total=total)


@router.post(
    "/tax-codes",
    response_model=s.TaxCodeRead,
    status_code=201,
    dependencies=[require(tax_codes.PERM_MANAGE)],
)
async def create_tax_code(payload: s.TaxCodeCreate, ctx: Access, uow: UowDep) -> s.TaxCodeRead:
    row = await tax_codes.create(
        uow.session,
        ctx,
        tax_codes.TaxCodeInput(
            code=payload.code,
            name=payload.name,
            tax_type=payload.tax_type,
            rate_pct=payload.rate_pct,
            applies_to=payload.applies_to,
            section_code=payload.section_code,
            is_active=payload.is_active,
        ),
    )
    return s.TaxCodeRead.model_validate(row)


@router.patch(
    "/tax-codes/{tax_code_id}",
    response_model=s.TaxCodeRead,
    dependencies=[require(tax_codes.PERM_MANAGE)],
)
async def update_tax_code(
    tax_code_id: UUID, payload: s.TaxCodeEdit, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.TaxCodeRead:
    row = await tax_codes.update(
        uow.session,
        ctx,
        tax_code_id,
        tax_codes.TaxCodeEdit(
            name=payload.name,
            rate_pct=payload.rate_pct,
            section_code=payload.section_code,
            is_active=payload.is_active,
        ),
        expected_version=if_match,
    )
    # The update just flushed, which expires server-computed columns
    # (updated_at); refresh before reading them back synchronously below.
    await uow.session.refresh(row)
    return s.TaxCodeRead.model_validate(row)


# -----------------------------------------------------------------------------
# Vendor invoices
# -----------------------------------------------------------------------------


def _invoice_items_input(payload: s.VendorInvoiceCreate) -> list[vendor_invoices.InvoiceItemInput]:
    return [
        vendor_invoices.InvoiceItemInput(
            quantity=line.quantity,
            rate=line.rate,
            po_item_id=line.po_item_id,
            grn_item_id=line.grn_item_id,
            material_id=line.material_id,
            description=line.description,
            unit_id=line.unit_id,
            tax_code_id=line.tax_code_id,
            tax_pct=line.tax_pct,
            account_id=line.account_id,
        )
        for line in payload.items
    ]


def _invoice_may(ctx: AccessContext, permission: str, invoice: VendorInvoice) -> bool:
    try:
        assert_in_scope(ctx, permission, company_id=invoice.company_id, entity="Vendor invoice")
    except Exception:  # noqa: BLE001 - any refusal simply means "no"
        return False
    return True


async def _invoice_detail(
    session: AsyncSession, ctx: AccessContext, invoice: VendorInvoice
) -> s.VendorInvoiceRead:
    # Freshly submit/match/approve leaves server-computed columns expired.
    await session.refresh(invoice)
    await session.refresh(invoice, attribute_names=["items"])

    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={invoice.vendor_id}
    )
    account_ids = {i.account_id for i in invoice.items if i.account_id is not None}
    accounts_by_id = {
        a.id: a
        for a in (await session.execute(select(Account).where(Account.id.in_(account_ids))))
        .scalars()
        .all()
    }
    materials = await material_lookup.materials(
        session,
        company_id=ctx.company_id,
        material_ids={i.material_id for i in invoice.items if i.material_id},
    )
    units = await material_lookup.unit_codes(
        session,
        company_id=ctx.company_id,
        unit_ids={i.unit_id for i in invoice.items if i.unit_id},
    )
    matches = (
        (
            await session.execute(
                select(VendorInvoiceMatch).where(
                    VendorInvoiceMatch.invoice_item_id.in_([i.id for i in invoice.items])
                )
            )
        )
        .scalars()
        .all()
    )
    matches_by_item = {m.invoice_item_id: m for m in matches}

    po_number = None
    if invoice.purchase_order_id is not None:
        order = await po_lookup.order(
            session, company_id=ctx.company_id, purchase_order_id=invoice.purchase_order_id
        )
        po_number = order.po_number if order else None

    items = []
    for item in invoice.items:
        account = accounts_by_id.get(item.account_id) if item.account_id else None
        m = matches_by_item.get(item.id)
        items.append(
            s.InvoiceItemRead(
                id=item.id,
                line_no=item.line_no,
                po_item_id=item.po_item_id,
                grn_item_id=item.grn_item_id,
                material_id=item.material_id,
                material_name=materials[item.material_id].name
                if item.material_id in materials
                else None,
                description=item.description,
                quantity=item.quantity,
                unit_id=item.unit_id,
                unit_code=units.get(item.unit_id) if item.unit_id else None,
                rate=item.rate,
                tax_code_id=item.tax_code_id,
                tax_pct=item.tax_pct,
                tax_amount=item.tax_amount,
                amount=item.amount,
                account_id=item.account_id,
                account_code=account.code if account else None,
                match_type=m.match_type if m else None,
                qty_variance=m.qty_variance if m else None,
                rate_variance=m.rate_variance if m else None,
                amount_variance=m.amount_variance if m else None,
                within_tolerance=m.within_tolerance if m else None,
            )
        )

    view = s.VendorInvoiceRead.model_validate(invoice)
    view.vendor_name = vendors[invoice.vendor_id].name if invoice.vendor_id in vendors else None
    view.purchase_order_number = po_number
    view.outstanding_amount = invoice.total_amount - invoice.paid_amount
    view.items = items
    status = VendorInvoiceStatus(invoice.status)
    can_edit = status.is_editable and _invoice_may(ctx, vendor_invoices.PERM_CREATE, invoice)
    view.can_edit = can_edit
    view.can_delete = status is VendorInvoiceStatus.DRAFT and can_edit
    view.can_match = can_edit and _invoice_may(ctx, vendor_invoices.PERM_MATCH, invoice)
    view.can_dispute = status is VendorInvoiceStatus.MATCHED and _invoice_may(
        ctx, vendor_invoices.PERM_APPROVE, invoice
    )
    view.can_approve = status is VendorInvoiceStatus.MATCHED and _invoice_may(
        ctx, vendor_invoices.PERM_APPROVE, invoice
    )
    return view


def _invoice_list_view(invoice: VendorInvoice, vendor_name: str | None) -> s.VendorInvoiceListItem:
    item = s.VendorInvoiceListItem.model_validate(invoice)
    item.vendor_name = vendor_name
    item.outstanding_amount = invoice.total_amount - invoice.paid_amount
    return item


@router.get(
    "/vendor-invoices",
    response_model=Page[s.VendorInvoiceListItem],
    dependencies=[require(vendor_invoices.PERM_VIEW)],
)
async def list_vendor_invoices(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    vendor_id: UUID | None = None,
) -> Page[s.VendorInvoiceListItem]:
    rows, total = await vendor_invoices.list_invoices(
        session,
        ctx,
        page=page,
        search=q,
        filters={"status": status, "vendor_id": vendor_id},
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={r.vendor_id for r in rows}
    )
    items = [
        _invoice_list_view(r, vendors[r.vendor_id].name if r.vendor_id in vendors else None)
        for r in rows
    ]
    return Page.of(items, params=page, total=total)


@router.post(
    "/vendor-invoices",
    response_model=s.VendorInvoiceRead,
    status_code=201,
    dependencies=[require(vendor_invoices.PERM_CREATE)],
    summary="Enter a vendor's bill. It posts nothing until it is matched and approved.",
)
async def create_vendor_invoice(
    payload: s.VendorInvoiceCreate, ctx: Access, uow: UowDep
) -> s.VendorInvoiceRead:
    invoice = await vendor_invoices.create(
        uow.session,
        ctx,
        vendor_invoices.VendorInvoiceInput(
            vendor_id=payload.vendor_id,
            vendor_invoice_ref=payload.vendor_invoice_ref,
            invoice_date=payload.invoice_date,
            purchase_order_id=payload.purchase_order_id,
            due_date=payload.due_date,
            withholding_amount=payload.withholding_amount,
            items=_invoice_items_input(payload),
        ),
    )
    return await _invoice_detail(uow.session, ctx, invoice)


@router.get(
    "/vendor-invoices/{invoice_id}",
    response_model=s.VendorInvoiceRead,
    dependencies=[require(vendor_invoices.PERM_VIEW)],
)
async def get_vendor_invoice(
    invoice_id: UUID, ctx: Access, session: SessionDep
) -> s.VendorInvoiceRead:
    return await _invoice_detail(session, ctx, await vendor_invoices.get(session, ctx, invoice_id))


@router.put(
    "/vendor-invoices/{invoice_id}",
    response_model=s.VendorInvoiceRead,
    dependencies=[require(vendor_invoices.PERM_CREATE)],
    summary="Edit a draft (or a disputed invoice, which can be corrected and rematched)",
)
async def update_vendor_invoice(
    invoice_id: UUID,
    payload: s.VendorInvoiceCreate,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> s.VendorInvoiceRead:
    invoice = await vendor_invoices.update(
        uow.session,
        ctx,
        invoice_id,
        vendor_invoices.VendorInvoiceInput(
            vendor_id=payload.vendor_id,
            vendor_invoice_ref=payload.vendor_invoice_ref,
            invoice_date=payload.invoice_date,
            purchase_order_id=payload.purchase_order_id,
            due_date=payload.due_date,
            withholding_amount=payload.withholding_amount,
            items=_invoice_items_input(payload),
        ),
        expected_version=if_match,
    )
    return await _invoice_detail(uow.session, ctx, invoice)


@router.delete(
    "/vendor-invoices/{invoice_id}",
    status_code=204,
    dependencies=[require(vendor_invoices.PERM_CREATE)],
    summary="Delete a draft that never happened",
)
async def delete_vendor_invoice(invoice_id: UUID, ctx: Access, uow: UowDep) -> None:
    await vendor_invoices.delete_draft(uow.session, ctx, invoice_id)


@router.post(
    "/vendor-invoices/{invoice_id}/match",
    response_model=s.VendorInvoiceRead,
    dependencies=[require(vendor_invoices.PERM_MATCH)],
    summary="Check every line against its PO/GRN within tolerance (docs/02 §8)",
)
async def match_vendor_invoice(
    invoice_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.VendorInvoiceRead:
    invoice = await vendor_invoices.match(uow.session, ctx, invoice_id, expected_version=if_match)
    return await _invoice_detail(uow.session, ctx, invoice)


@router.post(
    "/vendor-invoices/{invoice_id}/dispute",
    response_model=s.VendorInvoiceRead,
    dependencies=[require(vendor_invoices.PERM_APPROVE)],
    summary="Override a matched invoice back to disputed by hand",
)
async def dispute_vendor_invoice(
    invoice_id: UUID, payload: s.CancelBody, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.VendorInvoiceRead:
    invoice = await vendor_invoices.dispute(
        uow.session, ctx, invoice_id, payload.reason, expected_version=if_match
    )
    return await _invoice_detail(uow.session, ctx, invoice)


@router.post(
    "/vendor-invoices/{invoice_id}/approve",
    response_model=s.VendorInvoiceRead,
    dependencies=[require(vendor_invoices.PERM_APPROVE)],
    summary="Post the invoice: clears each matched line's GRN accrual into payable",
)
async def approve_vendor_invoice(
    invoice_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.VendorInvoiceRead:
    invoice = await vendor_invoices.approve(uow.session, ctx, invoice_id, expected_version=if_match)
    return await _invoice_detail(uow.session, ctx, invoice)


# -----------------------------------------------------------------------------
# Payables aging
# -----------------------------------------------------------------------------


@router.get(
    "/payables/aging",
    response_model=s.PayablesAgingRead,
    dependencies=[require(vendor_invoices.PERM_VIEW)],
)
async def get_payables_aging(
    ctx: Access, session: SessionDep, as_of: date | None = None
) -> s.PayablesAgingRead:
    cutoff = as_of or today_utc()
    rows = await vendor_invoices.payables_aging(session, ctx, as_of=cutoff)
    buckets = [s.AgeingBucketRead.model_validate(r) for r in rows]
    return s.PayablesAgingRead(
        as_of=cutoff, rows=buckets, total=sum((b.total for b in buckets), Decimal(0))
    )


# -----------------------------------------------------------------------------
# Bank accounts
# -----------------------------------------------------------------------------


def _bank_account_view(row: BankAccount, account_code: str | None) -> s.BankAccountRead:
    view = s.BankAccountRead.model_validate(row)
    view.gl_account_code = account_code
    return view


async def _bank_account_views(
    session: AsyncSession, rows: list[BankAccount]
) -> list[s.BankAccountRead]:
    account_ids = {r.gl_account_id for r in rows}
    codes = {
        a.id: a.code
        for a in (await session.execute(select(Account).where(Account.id.in_(account_ids))))
        .scalars()
        .all()
    }
    return [_bank_account_view(r, codes.get(r.gl_account_id)) for r in rows]


@router.get(
    "/bank-accounts",
    response_model=Page[s.BankAccountRead],
    dependencies=[require(bank_accounts.PERM_VIEW)],
)
async def list_bank_accounts(
    ctx: Access, session: SessionDep, page: PageDep, q: str | None = None
) -> Page[s.BankAccountRead]:
    rows, total = await bank_accounts.list_accounts(session, ctx, page=page, search=q, filters={})
    return Page.of(await _bank_account_views(session, rows), params=page, total=total)


@router.post(
    "/bank-accounts",
    response_model=s.BankAccountRead,
    status_code=201,
    dependencies=[require(bank_accounts.PERM_MANAGE)],
)
async def create_bank_account(
    payload: s.BankAccountCreate, ctx: Access, uow: UowDep
) -> s.BankAccountRead:
    row = await bank_accounts.create(
        uow.session,
        ctx,
        bank_accounts.BankAccountInput(
            account_title=payload.account_title,
            account_no=payload.account_no,
            bank_name=payload.bank_name,
            gl_account_id=payload.gl_account_id,
            iban=payload.iban,
            currency_code=payload.currency_code,
            opening_balance=payload.opening_balance,
            is_active=payload.is_active,
        ),
    )
    return (await _bank_account_views(uow.session, [row]))[0]


@router.patch(
    "/bank-accounts/{bank_account_id}",
    response_model=s.BankAccountRead,
    dependencies=[require(bank_accounts.PERM_MANAGE)],
)
async def update_bank_account(
    bank_account_id: UUID,
    payload: s.BankAccountEdit,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> s.BankAccountRead:
    row = await bank_accounts.update(
        uow.session,
        ctx,
        bank_account_id,
        bank_accounts.BankAccountEdit(
            account_title=payload.account_title,
            iban=payload.iban,
            bank_name=payload.bank_name,
            is_active=payload.is_active,
        ),
        expected_version=if_match,
    )
    await uow.session.refresh(row)
    return (await _bank_account_views(uow.session, [row]))[0]


# -----------------------------------------------------------------------------
# Payment requests
# -----------------------------------------------------------------------------


def _payment_request_input(payload: s.PaymentRequestCreate) -> payment_requests.RequestInput:
    return payment_requests.RequestInput(
        vendor_id=payload.vendor_id,
        amount=payload.amount,
        reason=payload.reason,
        priority=payload.priority.value,
        is_advance=payload.is_advance,
        currency_code=payload.currency_code,
    )


def _payment_request_may(ctx: AccessContext, permission: str, request: PaymentRequest) -> bool:
    try:
        assert_in_scope(ctx, permission, company_id=request.company_id, entity="Payment request")
    except Exception:  # noqa: BLE001 - any refusal simply means "no"
        return False
    return True


async def _payment_request_detail(
    session: AsyncSession, ctx: AccessContext, request: PaymentRequest
) -> s.PaymentRequestRead:
    await session.refresh(request)
    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={request.vendor_id}
    )
    view = s.PaymentRequestRead.model_validate(request)
    view.vendor_name = vendors[request.vendor_id].name if request.vendor_id in vendors else None
    editable = PaymentRequestStatus(request.status).is_editable
    view.can_edit = editable and _payment_request_may(ctx, payment_requests.PERM_CREATE, request)
    view.can_delete = request.status == PaymentRequestStatus.DRAFT.value and _payment_request_may(
        ctx, payment_requests.PERM_CREATE, request
    )
    view.can_submit = editable and _payment_request_may(ctx, payment_requests.PERM_CREATE, request)
    view.can_cancel = request.status in {
        PaymentRequestStatus.DRAFT.value,
        PaymentRequestStatus.REJECTED.value,
        PaymentRequestStatus.CHANGES_REQUESTED.value,
        PaymentRequestStatus.APPROVED.value,
    } and _payment_request_may(ctx, payment_requests.PERM_CREATE, request)
    return view


@router.get(
    "/payment-requests",
    response_model=Page[s.PaymentRequestListItem],
    dependencies=[require(payment_requests.PERM_VIEW)],
)
async def list_payment_requests(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    vendor_id: UUID | None = None,
) -> Page[s.PaymentRequestListItem]:
    rows, total = await payment_requests.list_requests(
        session, ctx, page=page, search=q, filters={"status": status, "vendor_id": vendor_id}
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={r.vendor_id for r in rows}
    )
    items = []
    for r in rows:
        item = s.PaymentRequestListItem.model_validate(r)
        item.vendor_name = vendors[r.vendor_id].name if r.vendor_id in vendors else None
        items.append(item)
    return Page.of(items, params=page, total=total)


@router.post(
    "/payment-requests",
    response_model=s.PaymentRequestRead,
    status_code=201,
    dependencies=[require(payment_requests.PERM_CREATE)],
    summary="Raise a request to pay a vendor (starts as a draft)",
)
async def create_payment_request(
    payload: s.PaymentRequestCreate, ctx: Access, uow: UowDep
) -> s.PaymentRequestRead:
    request = await payment_requests.create(uow.session, ctx, _payment_request_input(payload))
    return await _payment_request_detail(uow.session, ctx, request)


@router.get(
    "/payment-requests/{request_id}",
    response_model=s.PaymentRequestRead,
    dependencies=[require(payment_requests.PERM_VIEW)],
)
async def get_payment_request(
    request_id: UUID, ctx: Access, session: SessionDep
) -> s.PaymentRequestRead:
    return await _payment_request_detail(
        session, ctx, await payment_requests.get(session, ctx, request_id)
    )


@router.put(
    "/payment-requests/{request_id}",
    response_model=s.PaymentRequestRead,
    dependencies=[require(payment_requests.PERM_CREATE)],
    summary="Replace a draft, rejected or returned request's content",
)
async def update_payment_request(
    request_id: UUID,
    payload: s.PaymentRequestCreate,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> s.PaymentRequestRead:
    request = await payment_requests.update(
        uow.session,
        ctx,
        request_id,
        _payment_request_input(payload),
        expected_version=if_match,
    )
    return await _payment_request_detail(uow.session, ctx, request)


@router.delete(
    "/payment-requests/{request_id}",
    status_code=204,
    dependencies=[require(payment_requests.PERM_CREATE)],
    summary="Delete a draft that never happened",
)
async def delete_payment_request(request_id: UUID, ctx: Access, uow: UowDep) -> None:
    await payment_requests.delete_draft(uow.session, ctx, request_id)


@router.post(
    "/payment-requests/{request_id}/submit",
    response_model=s.PaymentRequestRead,
    dependencies=[require(payment_requests.PERM_CREATE)],
    summary="Submit for approval — routed by the active payment-request workflow",
)
async def submit_payment_request(
    request_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> s.PaymentRequestRead:
    request = await payment_requests.submit(uow.session, ctx, request_id, expected_version=if_match)
    return await _payment_request_detail(uow.session, ctx, request)


@router.post(
    "/payment-requests/{request_id}/cancel",
    response_model=s.PaymentRequestRead,
    dependencies=[require(payment_requests.PERM_CREATE)],
)
async def cancel_payment_request(
    request_id: UUID, payload: s.CancelBody, ctx: Access, uow: UowDep
) -> s.PaymentRequestRead:
    request = await payment_requests.cancel(uow.session, ctx, request_id, payload.reason)
    return await _payment_request_detail(uow.session, ctx, request)


# -----------------------------------------------------------------------------
# Payments
# -----------------------------------------------------------------------------


async def _payment_detail(
    session: AsyncSession, ctx: AccessContext, payment: Payment
) -> s.PaymentRead:
    await session.refresh(payment)
    await session.refresh(payment, attribute_names=["allocations"])
    vendors = await vendor_lookup.vendors(
        session,
        company_id=ctx.company_id,
        vendor_ids={payment.vendor_id} if payment.vendor_id else set(),
    )
    request_number = None
    if payment.payment_request_id is not None:
        request_number = await session.scalar(
            select(PaymentRequest.request_number).where(
                PaymentRequest.id == payment.payment_request_id
            )
        )
    bank_title = None
    if payment.bank_account_id is not None:
        bank = await bank_accounts.get(session, ctx, payment.bank_account_id)
        bank_title = bank.account_title
    invoice_numbers = {}
    if payment.allocations:
        rows = await session.execute(
            select(VendorInvoice.id, VendorInvoice.invoice_number).where(
                VendorInvoice.id.in_({a.invoice_id for a in payment.allocations})
            )
        )
        invoice_numbers = dict(rows.tuples().all())

    view = s.PaymentRead.model_validate(payment)
    view.vendor_name = vendors[payment.vendor_id].name if payment.vendor_id in vendors else None
    view.payment_request_number = request_number
    view.bank_account_title = bank_title
    view.allocations = [
        s.PaymentAllocationRead(
            id=a.id,
            invoice_id=a.invoice_id,
            invoice_number=invoice_numbers.get(a.invoice_id),
            allocated_amount=a.allocated_amount,
            created_at=a.created_at,
        )
        for a in payment.allocations
    ]
    issued = payment.status == PaymentStatus.ISSUED.value
    can_execute = ctx.has(payments.PERM_EXECUTE)
    view.can_allocate = issued and can_execute
    view.can_mark_cleared = issued and can_execute
    view.can_cancel = issued and can_execute
    return view


@router.get(
    "/payments", response_model=Page[s.PaymentListItem], dependencies=[require(payments.PERM_VIEW)]
)
async def list_payments(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    vendor_id: UUID | None = None,
) -> Page[s.PaymentListItem]:
    rows, total = await payments.list_payments(
        session, ctx, page=page, search=q, filters={"status": status, "vendor_id": vendor_id}
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={r.vendor_id for r in rows if r.vendor_id}
    )
    items = []
    for r in rows:
        item = s.PaymentListItem.model_validate(r)
        item.vendor_name = vendors[r.vendor_id].name if r.vendor_id in vendors else None
        items.append(item)
    return Page.of(items, params=page, total=total)


@router.post(
    "/payments",
    response_model=s.PaymentRead,
    status_code=201,
    dependencies=[require(payments.PERM_EXECUTE)],
    summary="Execute an approved payment request — posts to the GL immediately",
)
async def create_payment(payload: s.PaymentCreate, ctx: Access, uow: UowDep) -> s.PaymentRead:
    payment = await payments.create(
        uow.session,
        ctx,
        payments.PaymentInput(
            payment_request_id=payload.payment_request_id,
            payment_date=payload.payment_date,
            method=payload.method.value,
            bank_account_id=payload.bank_account_id,
            instrument_no=payload.instrument_no,
            withholding_amount=payload.withholding_amount,
        ),
    )
    return await _payment_detail(uow.session, ctx, payment)


@router.get(
    "/payments/{payment_id}",
    response_model=s.PaymentRead,
    dependencies=[require(payments.PERM_VIEW)],
)
async def get_payment(payment_id: UUID, ctx: Access, session: SessionDep) -> s.PaymentRead:
    return await _payment_detail(session, ctx, await payments.get(session, ctx, payment_id))


@router.post(
    "/payments/{payment_id}/allocate",
    response_model=s.PaymentRead,
    dependencies=[require(payments.PERM_EXECUTE)],
    summary="Settle one or more invoices against this payment",
)
async def allocate_payment(
    payment_id: UUID, payload: s.AllocateBody, ctx: Access, uow: UowDep
) -> s.PaymentRead:
    payment = await payments.allocate(
        uow.session,
        ctx,
        payment_id,
        [
            payments.AllocationInput(invoice_id=i.invoice_id, allocated_amount=i.allocated_amount)
            for i in payload.items
        ],
    )
    return await _payment_detail(uow.session, ctx, payment)


@router.post(
    "/payments/{payment_id}/mark-cleared",
    response_model=s.PaymentRead,
    dependencies=[require(payments.PERM_EXECUTE)],
    summary="Record the bank's own confirmation — no GL effect",
)
async def mark_payment_cleared(payment_id: UUID, ctx: Access, uow: UowDep) -> s.PaymentRead:
    payment = await payments.mark_cleared(uow.session, ctx, payment_id)
    return await _payment_detail(uow.session, ctx, payment)


@router.post(
    "/payments/{payment_id}/cancel",
    response_model=s.PaymentRead,
    dependencies=[require(payments.PERM_EXECUTE)],
    summary="Reverse an issued (not yet cleared) payment: its GL entry, allocations and request",
)
async def cancel_payment(
    payment_id: UUID, payload: s.CancelBody, ctx: Access, uow: UowDep
) -> s.PaymentRead:
    payment = await payments.cancel(uow.session, ctx, payment_id, payload.reason)
    return await _payment_detail(uow.session, ctx, payment)
