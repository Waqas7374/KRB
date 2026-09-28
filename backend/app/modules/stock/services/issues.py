"""Stock issues: material handed out of a store.

An issue is raised as a draft and posted when the goods actually leave. Posting
writes one ledger row per line at the store's **current average cost** (an issue
never changes the average), which is what the job or person is charged.
Cancelling a posted issue returns the stock with contra rows at the original
cost, like any other correction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, VersionConflictError
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope
from app.core.types import today_utc, utcnow
from app.modules.finance.domain.enums import JournalSourceType
from app.modules.finance.services import journal_entries, posting_rules
from app.modules.finance.services import ledger as gl
from app.modules.inventory.domain.enums import TxnType
from app.modules.inventory.services import ledger
from app.modules.masterdata.services.conversion import UnitConverter
from app.modules.stock.domain.enums import IssueStatus
from app.modules.stock.models import StockIssue, StockIssueItem
from app.modules.stock.schemas import IssueCreate
from app.modules.stock.services import common
from app.platform.numbering import DocumentType

PERM_VIEW = "inventory.view"
PERM_ISSUE = "inventory.issue"


def repository(session: AsyncSession) -> ScopedRepository[StockIssue]:
    return ScopedRepository(
        session,
        StockIssue,
        entity_name="Stock issue",
        sortable={"issue_number", "status", "issue_date", "created_at", "updated_at"},
        searchable=("issue_number", "issued_to_name", "purpose"),
        default_sort="-created_at",
    )


def _in_scope(ctx: AccessContext, permission: str, issue: StockIssue) -> None:
    assert_in_scope(
        ctx,
        permission,
        company_id=issue.company_id,
        project_id=issue.project_id,
        site_id=issue.site_id,
        entity="Stock issue",
    )


def _check_version(issue: StockIssue, expected: int | None) -> None:
    if expected is not None and issue.version != expected:
        raise VersionConflictError(
            f"{issue.issue_number} was changed by someone else (version {issue.version}, "
            f"you had {expected}). Reload and try again."
        )


async def _get_for_update(session: AsyncSession, ctx: AccessContext, issue_id: UUID) -> StockIssue:
    issue = await repository(session).get_for_update(ctx, PERM_ISSUE, issue_id)
    await session.refresh(issue, attribute_names=["items"])
    return issue


async def create(session: AsyncSession, ctx: AccessContext, data: IssueCreate) -> StockIssue:
    warehouse = await common.warehouse_in_scope(session, ctx, data.warehouse_id, PERM_ISSUE)
    materials = await common.stockable_materials(
        session, ctx, [line.material_id for line in data.lines]
    )
    on = data.issue_date or today_utc()
    converter = UnitConverter(session, ctx.company_id)
    for line in data.lines:
        # Fail now, at the counter, rather than when someone tries to post.
        await common.to_base(
            converter,
            quantity=line.quantity,
            unit_id=line.unit_id,
            material=materials[line.material_id],
            on=on,
        )

    issue = StockIssue(
        company_id=ctx.company_id,
        issue_number=await common.next_doc_number(session, ctx, DocumentType.STOCK_ISSUE),
        status=IssueStatus.DRAFT.value,
        warehouse_id=warehouse.id,
        project_id=warehouse.project_id,
        site_id=warehouse.site_id,
        issued_to_type=data.issued_to_type.value,
        issued_to_name=data.issued_to_name.strip(),
        purpose=data.purpose.strip(),
        issue_date=on,
        remarks=data.remarks,
        created_by_id=ctx.user_id,
        items=[
            StockIssueItem(
                line_no=index + 1,
                material_id=line.material_id,
                quantity=line.quantity,
                unit_id=line.unit_id,
                remarks=line.remarks,
                created_by_id=ctx.user_id,
            )
            for index, line in enumerate(data.lines)
        ],
    )
    session.add(issue)
    await session.flush()
    return issue


async def post(
    session: AsyncSession, ctx: AccessContext, issue_id: UUID, *, expected_version: int | None
) -> StockIssue:
    issue = await _get_for_update(session, ctx, issue_id)
    _check_version(issue, expected_version)
    _in_scope(ctx, PERM_ISSUE, issue)
    if issue.status != IssueStatus.DRAFT.value:
        raise BusinessRuleError(
            "issue_not_draft",
            f"{issue.issue_number} is {common.human(issue.status)}; it cannot be posted.",
        )
    materials = await common.stockable_materials(session, ctx, [i.material_id for i in issue.items])
    converter = UnitConverter(session, ctx.company_id)
    for item in issue.items:
        base = await common.to_base(
            converter,
            quantity=item.quantity,
            unit_id=item.unit_id,
            material=materials[item.material_id],
            on=issue.issue_date,
        )
        txn = await ledger.post(
            session,
            ctx,
            ledger.MovementRequest(
                txn_type=TxnType.ISSUE_OUT,
                warehouse_id=issue.warehouse_id,
                material_id=item.material_id,
                quantity=base,
                source_type="STOCK_ISSUE",
                source_id=issue.id,
                source_line_id=item.id,
                transaction_date=issue.issue_date,
                remarks=issue.issue_number,
            ),
        )
        item.base_quantity = base
        item.unit_cost = txn.unit_cost
        item.value = txn.value_out
        item.inventory_txn_id = txn.id
    issue.status = IssueStatus.ISSUED.value
    issue.issued_at = utcnow()
    issue.issued_by_id = ctx.user_id
    issue.version += 1
    issue.updated_by_id = ctx.user_id
    await _post_to_gl(session, ctx, issue)
    await session.flush()
    return issue


async def _post_to_gl(session: AsyncSession, ctx: AccessContext, issue: StockIssue) -> None:
    """What left the store becomes a development cost the moment it does
    (docs/10: inventory auto-posting), at the same total value the ledger
    lines above were just posted at."""
    total = sum((item.value for item in issue.items if item.value), Decimal(0))
    if total == 0:
        return
    rule = await posting_rules.resolve_or_fail(
        session,
        ctx,
        source_type=JournalSourceType.INVENTORY,
        event="ISSUE",
        context={},
        what="a stock issue",
    )
    dims = {"project_id": issue.project_id, "site_id": issue.site_id}
    entry = await gl.post_system_entry(
        session,
        ctx,
        source_type=JournalSourceType.INVENTORY,
        source_id=issue.id,
        entry_date=issue.issue_date,
        description=f"Material issued: {issue.issue_number}",
        reference=None,
        lines=[
            gl.debit(rule.debit_account_id, total, **dims),
            gl.credit(rule.credit_account_id, total, **dims),
        ],
    )
    issue.journal_entry_id = entry.id


async def cancel(
    session: AsyncSession, ctx: AccessContext, issue_id: UUID, reason: str
) -> StockIssue:
    issue = await _get_for_update(session, ctx, issue_id)
    _in_scope(ctx, PERM_ISSUE, issue)
    if issue.status == IssueStatus.CANCELLED.value:
        raise BusinessRuleError("issue_cancelled", f"{issue.issue_number} is already cancelled.")
    if len(reason.strip()) < 5:
        raise common.fail("reason", "Say why this issue is being cancelled")
    if issue.status == IssueStatus.ISSUED.value:
        # Put the goods back at the cost they left at, in the reverse order.
        for item in sorted(issue.items, key=lambda i: i.line_no, reverse=True):
            if item.inventory_txn_id is not None:
                await ledger.reverse_by_id(
                    session,
                    ctx,
                    item.inventory_txn_id,
                    source_type="STOCK_ISSUE_CANCEL",
                    source_id=issue.id,
                    remarks=f"{issue.issue_number} cancelled",
                )
        if issue.journal_entry_id is not None:
            await journal_entries.reverse_system(
                session,
                ctx,
                issue.journal_entry_id,
                reason=f"{issue.issue_number} cancelled: {reason}",
            )
    issue.status = IssueStatus.CANCELLED.value
    issue.cancelled_at = utcnow()
    issue.cancel_reason = reason.strip()
    issue.version += 1
    issue.updated_by_id = ctx.user_id
    await session.flush()
    return issue


async def list_issues(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[StockIssue], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, issue_id: UUID) -> StockIssue:
    return await repository(session).get(ctx, PERM_VIEW, issue_id)


def total_value(issue: StockIssue) -> Decimal | None:
    values = [i.value for i in issue.items if i.value is not None]
    return sum(values, Decimal(0)) if values else None
