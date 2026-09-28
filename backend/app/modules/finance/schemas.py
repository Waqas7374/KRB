"""Finance API contracts: chart of accounts, periods, journal entries, reports."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.finance.domain.enums import AccountType, JournalSourceType


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


# -----------------------------------------------------------------------------
# Accounts
# -----------------------------------------------------------------------------


class AccountCreate(ApiModel):
    code: Annotated[str, Field(min_length=1, max_length=20)]
    name: Annotated[str, Field(min_length=2, max_length=160)]
    account_type: AccountType
    parent_id: UUID | None = None
    requires_project: bool = False
    requires_cost_center: bool = False
    description: Annotated[str | None, Field(max_length=500)] = None


class AccountEdit(ApiModel):
    name: Annotated[str | None, Field(min_length=2, max_length=160)] = None
    description: Annotated[str | None, Field(max_length=500)] = None
    requires_project: bool | None = None
    requires_cost_center: bool | None = None
    is_active: bool | None = None


class AccountRead(ApiModel):
    id: UUID
    code: str
    name: str
    account_type: str
    normal_balance: str
    parent_id: UUID | None
    path: str
    is_postable: bool
    requires_project: bool
    requires_cost_center: bool
    is_active: bool
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class AccountNodeRead(ApiModel):
    account: AccountRead
    children: list[AccountNodeRead] = []


# -----------------------------------------------------------------------------
# Periods
# -----------------------------------------------------------------------------


class GenerateFiscalYear(ApiModel):
    fiscal_year: Annotated[int, Field(ge=2000, le=2100)]


class PeriodRead(ApiModel):
    id: UUID
    fiscal_year: int
    period_no: int
    start_date: date
    end_date: date
    status: str
    closed_by_id: UUID | None
    closed_at: datetime | None
    version: int


# -----------------------------------------------------------------------------
# Journal entries
# -----------------------------------------------------------------------------


class JournalLineIn(ApiModel):
    account_id: UUID
    debit: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)] = Decimal(0)
    credit: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)] = Decimal(0)
    description: Annotated[str | None, Field(max_length=300)] = None
    project_id: UUID | None = None
    phase_id: UUID | None = None
    site_id: UUID | None = None
    department_id: UUID | None = None
    cost_center_id: UUID | None = None
    vendor_id: UUID | None = None
    customer_id: UUID | None = None
    employee_id: UUID | None = None
    material_id: UUID | None = None

    @model_validator(mode="after")
    def _one_side(self) -> JournalLineIn:
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("A line is a debit or a credit, never both and never neither")
        return self


class JournalEntryCreate(ApiModel):
    entry_date: date
    description: Annotated[str, Field(min_length=3, max_length=500)]
    reference: Annotated[str | None, Field(max_length=120)] = None
    lines: Annotated[list[JournalLineIn], Field(min_length=2, max_length=200)]


class CancelBody(ApiModel):
    reason: Annotated[str, Field(min_length=5, max_length=500)]


class JournalLineRead(ApiModel):
    id: UUID
    line_no: int
    account_id: UUID
    account_code: str | None = None
    account_name: str | None = None
    debit: Decimal
    credit: Decimal
    description: str | None
    project_id: UUID | None
    project_code: str | None = None
    phase_id: UUID | None
    site_id: UUID | None
    site_code: str | None = None
    department_id: UUID | None
    cost_center_id: UUID | None
    cost_center_code: str | None = None
    vendor_id: UUID | None
    vendor_name: str | None = None
    customer_id: UUID | None
    employee_id: UUID | None
    material_id: UUID | None
    material_name: str | None = None


class JournalEntryListItem(ApiModel):
    id: UUID
    je_number: str
    entry_date: date
    source_type: str
    description: str
    reference: str | None
    status: str
    total_debit: Decimal
    total_credit: Decimal
    posted_at: datetime | None
    updated_at: datetime


class JournalEntryRead(JournalEntryListItem):
    source_id: UUID | None
    period_id: UUID
    fiscal_year: int | None = None
    period_no: int | None = None
    submitted_at: datetime | None
    approval_request_id: UUID | None
    decision_reason: str | None
    posted_by_id: UUID | None
    reversal_of_id: UUID | None
    reversed_at: datetime | None
    version: int
    created_at: datetime
    lines: list[JournalLineRead]
    can_edit: bool = False
    can_submit: bool = False
    can_withdraw: bool = False
    can_delete: bool = False
    can_reverse: bool = False


# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------


class TrialBalanceRowRead(ApiModel):
    account_id: UUID
    code: str
    name: str
    account_type: str
    debit: Decimal
    credit: Decimal


class TrialBalanceRead(ApiModel):
    as_of: date
    rows: list[TrialBalanceRowRead]
    total_debit: Decimal
    total_credit: Decimal


class GeneralLedgerRowRead(ApiModel):
    id: UUID
    je_id: UUID
    je_number: str
    entry_date: date
    description: str
    line_description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class GeneralLedgerRead(ApiModel):
    account_id: UUID
    account_code: str
    account_name: str
    opening_balance: Decimal
    rows: list[GeneralLedgerRowRead]
    closing_balance: Decimal


# -----------------------------------------------------------------------------
# Posting rules
# -----------------------------------------------------------------------------


class PostingRuleCreate(ApiModel):
    source_type: JournalSourceType
    event: Annotated[str, Field(min_length=1, max_length=40)]
    debit_account_id: UUID
    credit_account_id: UUID
    name: Annotated[str | None, Field(max_length=160)] = None
    condition: Any = None
    priority: int = 0
    is_active: bool = True


class PostingRuleEdit(ApiModel):
    name: Annotated[str | None, Field(max_length=160)] = None
    condition: Any = None
    debit_account_id: UUID | None = None
    credit_account_id: UUID | None = None
    priority: int | None = None
    is_active: bool | None = None


class PostingRuleRead(ApiModel):
    id: UUID
    source_type: str
    event: str
    name: str | None
    condition: Any = None
    debit_account_id: UUID
    debit_account_code: str | None = None
    credit_account_id: UUID
    credit_account_code: str | None = None
    priority: int
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Budgets
# -----------------------------------------------------------------------------


class BudgetLineIn(ApiModel):
    account_id: UUID
    budgeted_amount: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
    phase_id: UUID | None = None
    cost_center_id: UUID | None = None
    material_category_id: UUID | None = None
    period_id: UUID | None = None


class BudgetCreate(ApiModel):
    project_id: UUID
    fiscal_year: Annotated[int, Field(ge=2000, le=2100)]
    name: Annotated[str, Field(min_length=2, max_length=160)]
    lines: Annotated[list[BudgetLineIn], Field(min_length=1, max_length=200)]


class BudgetRevise(ApiModel):
    # budget_line_id -> revised_amount
    revisions: dict[UUID, Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]]


class BudgetLineRead(ApiModel):
    id: UUID
    account_id: UUID
    account_code: str | None = None
    account_name: str | None = None
    phase_id: UUID | None
    phase_code: str | None = None
    cost_center_id: UUID | None
    cost_center_code: str | None = None
    material_category_id: UUID | None
    period_id: UUID | None
    budgeted_amount: Decimal
    revised_amount: Decimal | None
    committed_amount: Decimal
    actual_amount: Decimal
    # Neither is a database column (see finance/models.py's BudgetLine
    # docstring) — both are filled in by the route from the columns above.
    remaining_amount: Decimal = Decimal(0)
    variance_pct: Decimal | None = None


class BudgetListItem(ApiModel):
    id: UUID
    project_id: UUID
    project_code: str | None = None
    fiscal_year: int
    name: str
    status: str
    total_amount: Decimal
    approved_at: datetime | None
    version: int


class BudgetRead(BudgetListItem):
    approved_by_id: UUID | None
    closed_at: datetime | None
    lines: list[BudgetLineRead]
    can_edit: bool = False
    can_approve: bool = False
    can_revise: bool = False
    can_close: bool = False


class BudgetVsActualRead(ApiModel):
    """The report docs/07 §2 documents separately from the editable budget
    itself: the same lines, plus the totals a chart or a summary row wants
    without re-adding them client-side."""

    id: UUID
    project_id: UUID
    project_code: str | None = None
    fiscal_year: int
    name: str
    status: str
    lines: list[BudgetLineRead]
    total_budgeted: Decimal
    total_committed: Decimal
    total_actual: Decimal
    total_remaining: Decimal


class BudgetCommitmentRead(ApiModel):
    id: UUID
    budget_line_id: UUID
    source_type: str
    source_id: UUID
    amount: Decimal
    released_amount: Decimal
    status: str
    created_at: datetime
