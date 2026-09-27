"""Finance API contracts: chart of accounts, periods, journal entries, reports."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.finance.domain.enums import AccountType


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
