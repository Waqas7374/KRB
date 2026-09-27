"""Post to the general ledger. This is the one door every journal entry goes
through, whichever module raised it (docs/02 §8, docs/07 §3).

Two shapes of caller:

* **A subledger event that is already approved as its own document** — a
  posted GRN, an approved vendor invoice, an executed payment (from Phase 4b
  onward). It calls `post_system_entry`: build the entry and post it in the
  same breath, inside the caller's own transaction, so stock/AP/cash and the
  GL move together or not at all.
* **A person, entering a manual adjustment.** `journal_entries.py` calls
  `build_draft` to create it, then — on approval — `post` to bring it into
  force. A manual entry earns its place in the ledger through the approval
  engine; a system entry earns it by the approval its own document already had.

Either way, the database is what actually proves an entry balances: see the
deferred constraint trigger in the migration. This module also checks it
before ever reaching the database, so a mistake is a clear 422 and not a raw
constraint violation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, ValidationError
from app.core.types import utcnow
from app.modules.finance.domain.enums import JournalSourceType, JournalStatus, PeriodStatus
from app.modules.finance.models import Account, JournalEntry, JournalEntryLine
from app.modules.finance.services import periods
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import company_service, document_lookup
from app.modules.org.services.document_lookup import PlaceMismatchError
from app.modules.vendors.services import vendor_lookup
from app.platform.numbering import DocumentType, next_number


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


@dataclass(frozen=True, slots=True)
class LineInput:
    account_id: UUID
    debit: Decimal
    credit: Decimal
    description: str | None = None
    project_id: UUID | None = None
    phase_id: UUID | None = None
    site_id: UUID | None = None
    department_id: UUID | None = None
    cost_center_id: UUID | None = None
    vendor_id: UUID | None = None
    customer_id: UUID | None = None
    employee_id: UUID | None = None
    material_id: UUID | None = None


def debit(account_id: UUID, amount: Decimal, **dims: Any) -> LineInput:
    """A debit line, for the callers (GRN, invoices, payments...) that build
    entries as a plain list of postings rather than by hand."""
    return LineInput(account_id=account_id, debit=amount, credit=Decimal(0), **dims)


def credit(account_id: UUID, amount: Decimal, **dims: Any) -> LineInput:
    return LineInput(account_id=account_id, debit=Decimal(0), credit=amount, **dims)


def _rows_to_lines(entry_lines: list[JournalEntryLine]) -> list[LineInput]:
    return [
        LineInput(
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
        for line in entry_lines
    ]


def _line_rows(ctx: AccessContext, lines: list[LineInput]) -> list[JournalEntryLine]:
    return [
        JournalEntryLine(
            line_no=index + 1,
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
            created_by_id=ctx.user_id,
        )
        for index, line in enumerate(lines)
    ]


async def _validate_lines(
    session: AsyncSession, ctx: AccessContext, lines: list[LineInput]
) -> None:
    if not lines:
        raise BusinessRuleError("journal_entry_empty", "A journal entry needs at least one line.")
    errors: list[dict[str, str]] = []
    accounts = {
        a.id: a
        for a in (
            await session.execute(
                select(Account).where(
                    Account.id.in_({line.account_id for line in lines}),
                    Account.company_id == ctx.company_id,
                )
            )
        )
        .scalars()
        .all()
    }
    materials = await material_lookup.materials(
        session,
        company_id=ctx.company_id,
        material_ids={line.material_id for line in lines if line.material_id},
    )
    vendors = await vendor_lookup.vendors(
        session,
        company_id=ctx.company_id,
        vendor_ids={line.vendor_id for line in lines if line.vendor_id},
    )

    for index, line in enumerate(lines):
        prefix = f"lines.{index}"
        if (line.debit > 0) == (line.credit > 0):
            errors.append(
                {
                    "field": f"{prefix}.debit",
                    "code": "invalid",
                    "message": "A line is a debit or a credit, never both and never neither",
                }
            )
            continue
        account = accounts.get(line.account_id)
        if account is None:
            errors.append(
                {"field": f"{prefix}.account_id", "code": "invalid", "message": "Unknown account"}
            )
            continue
        if account.deleted_at is not None or not account.is_active:
            errors.append(
                {
                    "field": f"{prefix}.account_id",
                    "code": "invalid",
                    "message": f"{account.code} is not active",
                }
            )
        if not account.is_postable:
            errors.append(
                {
                    "field": f"{prefix}.account_id",
                    "code": "invalid",
                    "message": f"{account.code} is a group account; post to one of its children",
                }
            )
        if account.requires_project and line.project_id is None:
            errors.append(
                {
                    "field": f"{prefix}.project_id",
                    "code": "required",
                    "message": f"{account.code} needs a project",
                }
            )
        if account.requires_cost_center and line.cost_center_id is None:
            errors.append(
                {
                    "field": f"{prefix}.cost_center_id",
                    "code": "required",
                    "message": f"{account.code} needs a cost centre",
                }
            )
        if line.material_id is not None and line.material_id not in materials:
            errors.append(
                {"field": f"{prefix}.material_id", "code": "invalid", "message": "Unknown material"}
            )
        if line.vendor_id is not None and line.vendor_id not in vendors:
            errors.append(
                {"field": f"{prefix}.vendor_id", "code": "invalid", "message": "Unknown vendor"}
            )
        try:
            await document_lookup.resolve_place(
                session,
                company_id=ctx.company_id,
                project_id=line.project_id,
                site_id=line.site_id,
                department_id=line.department_id,
                phase_id=line.phase_id,
                cost_center_id=line.cost_center_id,
            )
        except PlaceMismatchError as exc:
            errors.append(
                {"field": f"{prefix}.{exc.field}", "code": "invalid", "message": str(exc)}
            )

    if errors:
        raise ValidationError("The journal entry is not valid.", errors=errors)

    total_debit = sum((line.debit for line in lines), Decimal(0))
    total_credit = sum((line.credit for line in lines), Decimal(0))
    if total_debit != total_credit:
        raise BusinessRuleError(
            "journal_entry_unbalanced",
            f"Debits ({total_debit:f}) do not equal credits ({total_credit:f}).",
        )


async def _build(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    source_type: JournalSourceType,
    source_id: UUID | None,
    entry_date: date,
    description: str,
    reference: str | None,
    lines: list[LineInput],
    status: JournalStatus,
) -> JournalEntry:
    await _validate_lines(session, ctx, lines)
    period = await periods.period_for_date(session, company_id=ctx.company_id, on=entry_date)
    if period is None:
        raise _fail("entry_date", "No accounting period covers this date. Ask finance to open one.")
    if period.status != PeriodStatus.OPEN.value:
        raise BusinessRuleError(
            "period_not_open",
            f"Period {period.period_no} of FY{period.fiscal_year} is {period.status.lower()}; "
            "nothing can be posted into it.",
        )

    total_debit = sum((line.debit for line in lines), Decimal(0))
    entry = JournalEntry(
        company_id=ctx.company_id,
        je_number=await next_number(
            session,
            company_id=ctx.company_id,
            doc_type=DocumentType.JOURNAL_ENTRY,
            fiscal_year_start_month=await company_service.fiscal_year_start_month(
                session, ctx.company_id
            ),
        ),
        entry_date=entry_date,
        period_id=period.id,
        source_type=source_type.value,
        source_id=source_id,
        description=description,
        reference=reference,
        status=status.value,
        total_debit=total_debit,
        total_credit=total_debit,  # equal, by the balance check above
        created_by_id=ctx.user_id,
        lines=_line_rows(ctx, lines),
    )
    session.add(entry)
    await session.flush()
    return entry


async def build_draft(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    entry_date: date,
    description: str,
    reference: str | None,
    lines: list[LineInput],
) -> JournalEntry:
    """A manual entry, saved but not yet posted (docs/02 §8: DRAFT)."""
    return await _build(
        session,
        ctx,
        source_type=JournalSourceType.MANUAL,
        source_id=None,
        entry_date=entry_date,
        description=description,
        reference=reference,
        lines=lines,
        status=JournalStatus.DRAFT,
    )


async def replace_draft_lines(
    session: AsyncSession, ctx: AccessContext, entry: JournalEntry, lines: list[LineInput]
) -> None:
    """Swap a draft's lines for a new set, all at once — a journal entry is
    only ever whole; there is no such thing as editing one line of it."""
    await _validate_lines(session, ctx, lines)
    entry.lines.clear()
    await session.flush()
    entry.lines.extend(_line_rows(ctx, lines))
    entry.total_debit = entry.total_credit = sum((line.debit for line in lines), Decimal(0))
    await session.flush()


async def post(
    session: AsyncSession, ctx: AccessContext, entry: JournalEntry, *, actor_id: UUID | None
) -> JournalEntry:
    """Bring a built entry into force. The period is re-checked here, not only
    at `build_draft` time: a manual entry can sit in approval for a while, and
    the period it targets may have been closed underneath it."""
    period = await periods.period_for_date(
        session, company_id=entry.company_id, on=entry.entry_date
    )
    if period is None or period.status != PeriodStatus.OPEN.value:
        raise BusinessRuleError(
            "period_not_open",
            f"The period for {entry.entry_date:%d %b %Y} is no longer open. Ask finance to "
            "reopen it, or edit the entry's date.",
        )
    entry.status = JournalStatus.POSTED.value
    entry.posted_at = utcnow()
    entry.posted_by_id = actor_id
    entry.version += 1
    await session.flush()
    return entry


async def post_system_entry(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    source_type: JournalSourceType,
    source_id: UUID,
    entry_date: date,
    description: str,
    reference: str | None,
    lines: list[LineInput],
) -> JournalEntry:
    """Build and post in one step, for an event that is already approved as
    its own document (a posted GRN, an approved invoice, an executed payment)."""
    entry = await _build(
        session,
        ctx,
        source_type=source_type,
        source_id=source_id,
        entry_date=entry_date,
        description=description,
        reference=reference,
        lines=lines,
        status=JournalStatus.DRAFT,  # never observed: post() below fires in the same transaction
    )
    return await post(session, ctx, entry, actor_id=ctx.user_id)


async def reverse(
    session: AsyncSession, ctx: AccessContext, original: JournalEntry, *, reason: str
) -> JournalEntry:
    """A new entry with every line's debit and credit swapped, posted at once.
    The original is marked REVERSED; nothing about it is edited or removed."""
    swapped = [
        LineInput(
            account_id=line.account_id,
            debit=line.credit,
            credit=line.debit,
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
        for line in _rows_to_lines(list(original.lines))
    ]
    reversal = await post_system_entry(
        session,
        ctx,
        source_type=JournalSourceType(original.source_type),
        source_id=original.source_id or original.id,
        entry_date=utcnow().date(),
        description=f"Reversal of {original.je_number}: {reason}",
        reference=original.reference,
        lines=swapped,
    )
    reversal.reversal_of_id = original.id
    original.status = JournalStatus.REVERSED.value
    original.reversed_at = utcnow()
    original.version += 1
    await session.flush()
    return reversal
