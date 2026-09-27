"""Chart of accounts, accounting periods and the general ledger (docs/02 §8).

The ledger is append-only by construction: a journal entry is created, posted
once, and a mistake is corrected with a **reversal** — a new entry, never an
edit to a posted one (`journal_entry_lines` on a posted entry carries no update
path in the service layer, and `journal_entries.status` only ever moves
DRAFT → PENDING_APPROVAL → POSTED → REVERSED, never backwards).

Balance is enforced twice, for different reasons: `moves_one_way` on each line
(a line is a debit or a credit, never both, never neither) is an ordinary CHECK;
`journal_lines_balanced` is a **deferred constraint trigger** (see the
migration) so that whatever a service does across one transaction, an entry's
debits equal its credits by the time that transaction commits — a structural
guarantee, not application discipline.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constraints import enum_check, non_negative
from app.core.db import BaseModel, CompanyModel, MasterDataModel, VersionMixin
from app.modules.finance.domain.enums import (
    AccountType,
    BudgetStatus,
    CommitmentSourceType,
    CommitmentStatus,
    JournalSourceType,
    JournalStatus,
    NormalBalance,
    PeriodStatus,
)


class Account(MasterDataModel):
    """One row of the chart of accounts. Only a leaf (`is_postable`) may be
    named on a journal line; a group account exists to total its children."""

    __tablename__ = "accounts"
    __audited__ = True
    # Company-wide reference data: see core/scoping.py. There is one chart of
    # accounts, not one per project or site.
    __scope_company_wide__ = True

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Set from account_type, not independently editable: there are no contra
    # accounts in v1 (see AccountType.normal_balance).
    normal_balance: Mapped[str] = mapped_column(String(2), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    # `{root id}.{...}.{this id}`, id-based so it never needs rewriting when a
    # code changes. Root accounts are their own single-segment path.
    path: Mapped[str] = mapped_column(String(2000), nullable=False)
    # False the moment a first child is attached (see accounts.services); a
    # group account is never itself postable again.
    is_postable: Mapped[bool] = mapped_column(nullable=False, default=True)
    requires_project: Mapped[bool] = mapped_column(nullable=False, default=False)
    requires_cost_center: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(String(500))

    children: Mapped[list[Account]] = relationship(
        back_populates="parent", lazy="noload", foreign_keys=[parent_id]
    )
    parent: Mapped[Account | None] = relationship(
        back_populates="children", remote_side="Account.id", lazy="joined"
    )

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_accounts_code"),
        enum_check("account_type", AccountType),
        enum_check("normal_balance", NormalBalance, name="normal_balance_valid"),
        Index("ix_accounts_path", "path"),
    )


class AccountingPeriod(CompanyModel, VersionMixin):
    """One month of the fiscal year (docs/12 Q1: FY starts 1 July). Posting
    into anything but an OPEN period is refused, in the service and by the
    journal entries' own foreign key discipline (see `ledger.py`)."""

    __tablename__ = "accounting_periods"
    __audited__ = True

    # The fiscal year's *starting* calendar year: FY2026-27 is fiscal_year=2026.
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_no: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=PeriodStatus.OPEN.value)
    closed_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("company_id", "fiscal_year", "period_no", name="uq_periods_fy_no"),
        Index("ix_periods_range", "company_id", "start_date", "end_date"),
        enum_check("status", PeriodStatus),
        CheckConstraint("period_no BETWEEN 1 AND 12", name="period_no_valid"),
        CheckConstraint("start_date < end_date", name="period_range_valid"),
    )


class JournalEntry(CompanyModel, VersionMixin):
    __tablename__ = "journal_entries"
    __audited__ = True

    je_number: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounting_periods.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Polymorphic on purpose (no FK): a GRN, an invoice, a payment... whatever
    # raised this entry. `source_type` says which table `source_id` names.
    source_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JournalStatus.DRAFT.value
    )
    total_debit: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_request_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    decision_reason: Mapped[str | None] = mapped_column(String(2000))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    # The entry that undid this one, or the one this entry undoes. At most one
    # reversal per entry: enforced by the partial unique index below.
    reversal_of_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="RESTRICT"), index=True
    )
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[JournalEntryLine]] = relationship(
        back_populates="entry",
        order_by="JournalEntryLine.line_no",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "je_number", name="uq_journal_entries_number"),
        Index(
            "uq_journal_entries_one_reversal",
            "reversal_of_id",
            unique=True,
            postgresql_where="reversal_of_id IS NOT NULL",
        ),
        enum_check("source_type", JournalSourceType),
        enum_check("status", JournalStatus),
        non_negative("total_debit"),
        non_negative("total_credit"),
    )


class JournalEntryLine(BaseModel):
    __tablename__ = "journal_entry_lines"
    __audited__ = True

    je_id: Mapped[UUID] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(String(300))

    # The dimension set (docs/02 §8). All nullable: which ones a line actually
    # needs depends on the account (`requires_project`, `requires_cost_center`)
    # and the source that raised the entry. `customer_id` and `employee_id`
    # carry no foreign key yet — those tables arrive with AR and with HR
    # (Phase 5) — and are validated only for shape until then.
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    phase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_phases.id", ondelete="RESTRICT")
    )
    site_id: Mapped[UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT")
    )
    cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="RESTRICT")
    )
    vendor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), index=True
    )
    customer_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    employee_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    material_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT")
    )

    entry: Mapped[JournalEntry] = relationship(back_populates="lines", lazy="noload")

    __table_args__ = (
        UniqueConstraint("je_id", "line_no", name="uq_journal_entry_lines_line"),
        non_negative("debit"),
        non_negative("credit"),
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="moves_one_way",
        ),
    )


class PostingRule(CompanyModel, VersionMixin):
    """Sub-ledger → GL mapping, as configuration (docs/02 §8): which accounts a
    GRN receipt (or, from a later slice, an invoice or a payment) debits and
    credits. `condition` picks between rules for the same `source_type` and
    `event` — e.g. a stockable material against a non-stockable one, or a
    receipt with a purchase order behind it against one without — using the
    same JSON language as approval workflows and business rules."""

    __tablename__ = "posting_rules"
    __audited__ = True
    __scope_company_wide__ = True

    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # A source_type's own sub-cases: "RECEIPT" today, "PAYMENT_ISSUED" and
    # similar arriving with 4c-4d. Free text on purpose — a new event needs no
    # migration, only a seed row and the code that calls `resolve()` for it.
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160))
    condition: Mapped[Any | None] = mapped_column(JSONB)
    debit_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    credit_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_posting_rules_lookup", "company_id", "source_type", "event", "is_active"),
        enum_check("source_type", JournalSourceType),
    )


class Budget(CompanyModel, VersionMixin):
    """One project's spending plan for one fiscal year (docs/02 §8). `REVISED`
    is entered automatically the first time a line's `revised_amount` is set
    on an approved budget — it is a fact about the budget's history, not a
    step someone chooses."""

    __tablename__ = "budgets"
    __audited__ = True

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BudgetStatus.DRAFT.value
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The sum of every line's own effective amount (revised_amount if the line
    # has one, else budgeted_amount) — kept in step by the service, not a
    # database trigger, since it changes on plain UPDATEs to child rows.
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    lines: Mapped[list[BudgetLine]] = relationship(
        back_populates="budget",
        order_by="BudgetLine.created_at",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "project_id", "fiscal_year", name="uq_budgets_project_fy"),
        enum_check("status", BudgetStatus),
        non_negative("total_amount"),
    )


class BudgetLine(BaseModel):
    """One line of a budget: a phase and/or cost centre, an account, and how
    much of it has been committed (by an approved PO) and actually spent (by a
    posted GRN) against the figure. `remaining_amount` and `variance_pct` are
    not stored — they are read straight off `budgeted_amount`/`revised_amount`
    minus `committed_amount`/`actual_amount` wherever a line is read, so they
    can never drift from the columns that back them."""

    __tablename__ = "budget_lines"
    __audited__ = True

    budget_id: Mapped[UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_phases.id", ondelete="RESTRICT")
    )
    cost_center_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cost_centers.id", ondelete="RESTRICT")
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    material_category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("material_categories.id", ondelete="RESTRICT")
    )
    period_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("accounting_periods.id", ondelete="RESTRICT")
    )
    budgeted_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    revised_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # Written only by `budgets.commit_purchase_order` / `.release_receipt` —
    # never edited by hand, so *committed → actual → remaining* stays true.
    committed_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    actual_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    budget: Mapped[Budget] = relationship(back_populates="lines", lazy="noload")

    __table_args__ = (
        # One line per (phase, cost centre, account) inside a budget — two
        # lines for the same combination would split what is really one figure.
        UniqueConstraint(
            "budget_id",
            "phase_id",
            "cost_center_id",
            "account_id",
            name="uq_budget_lines_scope",
        ),
        non_negative("budgeted_amount"),
        non_negative("committed_amount"),
        non_negative("actual_amount"),
        CheckConstraint(
            "revised_amount IS NULL OR revised_amount >= 0", name="revised_amount_non_negative"
        ),
    )


class BudgetCommitment(BaseModel):
    """Append-only (docs/02 §8): a row is added when a PO is approved and
    never removed, only released as GRNs come in against it. `source_id` is
    polymorphic (a purchase order today, a contract later) and carries no
    foreign key — budgets must not depend on procurement's tables."""

    __tablename__ = "budget_commitments"
    __audited__ = True

    budget_line_id: Mapped[UUID] = mapped_column(
        ForeignKey("budget_lines.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    released_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CommitmentStatus.OPEN.value
    )

    __table_args__ = (
        Index("ix_budget_commitments_source", "source_type", "source_id"),
        enum_check("source_type", CommitmentSourceType),
        enum_check("status", CommitmentStatus),
        non_negative("amount"),
        non_negative("released_amount"),
        CheckConstraint("released_amount <= amount", name="released_not_over_committed"),
    )
