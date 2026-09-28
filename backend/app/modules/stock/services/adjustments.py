"""Stock adjustments: correcting what the books say is on the shelf.

An adjustment creates or destroys stock without a receipt or an issue behind
it, so it is the document most worth being suspicious of. It therefore:

* always carries a **reason code and a note**;
* is always routed through the **approval engine** (the workflow decides who
  signs and at what size; a rule may approve a small one by itself);
* reaches the ledger **only through its approval**: the handler posts the lines
  in the same transaction that records the decision, so there is no window in
  which an approved adjustment has not moved stock, or a moved one was never
  approved.

Quantities are in the material's base unit, the ledger's own, so a correction
can never be ambiguous about what it changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext, system_access_context
from app.core.crud import ScopedRepository
from app.core.errors import BusinessRuleError, ValidationError, VersionConflictError
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope
from app.core.types import today_utc, utcnow
from app.modules.approvals.services import engine as approvals
from app.modules.approvals.services import registry
from app.modules.finance.domain.enums import JournalSourceType
from app.modules.finance.services import ledger as gl
from app.modules.finance.services import posting_rules
from app.modules.inventory.domain.enums import TxnType
from app.modules.inventory.services import ledger
from app.modules.masterdata.services import warehouse_lookup
from app.modules.org.services import document_lookup
from app.modules.stock.domain.enums import AdjustmentStatus
from app.modules.stock.models import StockAdjustment, StockAdjustmentItem
from app.modules.stock.schemas import AdjustmentCreate
from app.modules.stock.services import common
from app.platform.numbering import DocumentType

DOC_TYPE = "stock_adjustment"
PERM_VIEW = "inventory.view"
PERM_ADJUST = "inventory.adjust"
PERM_APPROVE = "inventory.approve_adjustment"

_VALUE = Decimal("0.0001")

CONTEXT_VARIABLES = frozenset(
    {
        "value_abs",
        "value_net",
        "quantity_abs",
        "reason_code",
        "is_write_off",
        "line_count",
        "warehouse.*",
        "project.*",
        "site.*",
        "requester.*",
    }
)


def repository(session: AsyncSession) -> ScopedRepository[StockAdjustment]:
    return ScopedRepository(
        session,
        StockAdjustment,
        entity_name="Stock adjustment",
        sortable={"adjustment_number", "status", "adjustment_date", "created_at", "updated_at"},
        searchable=("adjustment_number", "reason_note"),
        default_sort="-created_at",
    )


def _in_scope(ctx: AccessContext, permission: str, adjustment: StockAdjustment) -> None:
    assert_in_scope(
        ctx,
        permission,
        company_id=adjustment.company_id,
        project_id=adjustment.project_id,
        site_id=adjustment.site_id,
        entity="Stock adjustment",
    )


def _check_version(adjustment: StockAdjustment, expected: int | None) -> None:
    if expected is not None and adjustment.version != expected:
        raise VersionConflictError(
            f"{adjustment.adjustment_number} was changed by someone else "
            f"(version {adjustment.version}, you had {expected}). Reload and try again."
        )


async def _get_for_update(
    session: AsyncSession, ctx: AccessContext, adjustment_id: UUID
) -> StockAdjustment:
    adjustment = await repository(session).get_for_update(ctx, PERM_ADJUST, adjustment_id)
    await session.refresh(adjustment, attribute_names=["items"])
    return adjustment


# -----------------------------------------------------------------------------
# Raise and edit
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Line:
    material_id: UUID
    quantity_delta: Decimal
    unit_cost: Decimal | None
    value_delta: Decimal
    system_quantity: Decimal
    remarks: str | None


async def _build_lines(
    session: AsyncSession,
    ctx: AccessContext,
    warehouse: warehouse_lookup.WarehouseInfo,
    data: AdjustmentCreate,
) -> list[_Line]:
    await common.stockable_materials(session, ctx, [line.material_id for line in data.lines])
    lines: list[_Line] = []
    # What each material will hold once the earlier lines have been applied, so
    # two lines cannot together take out more than is there.
    remaining: dict[UUID, Decimal] = {}
    errors: list[dict[str, str]] = []
    for index, line in enumerate(data.lines):
        on_hand, average = await ledger.position(
            session, warehouse_id=warehouse.id, material_id=line.material_id
        )
        available = remaining.get(line.material_id, on_hand)
        delta = line.quantity_delta
        if delta > 0:
            cost = line.unit_cost if line.unit_cost is not None else average
            if cost <= 0:
                errors.append(
                    {
                        "field": f"lines.{index}.unit_cost",
                        "code": "required",
                        "message": "nothing is in stock to take a cost from; give the unit cost",
                    }
                )
                continue
        else:
            if -delta > available:
                errors.append(
                    {
                        "field": f"lines.{index}.quantity_delta",
                        "code": "invalid",
                        "message": f"only {available:f} is in stock to take out",
                    }
                )
                continue
            # An outgoing line leaves at the average cost of the day it is
            # posted; the average now is what the approver is shown.
            cost = average
        remaining[line.material_id] = available + delta
        lines.append(
            _Line(
                material_id=line.material_id,
                quantity_delta=delta,
                unit_cost=cost if delta > 0 else None,
                value_delta=(delta * cost).quantize(_VALUE, rounding=ROUND_HALF_UP),
                system_quantity=on_hand,
                remarks=line.remarks,
            )
        )
    if errors:
        raise ValidationError("The adjustment is not valid.", errors=errors)
    return lines


def _items(ctx: AccessContext, lines: list[_Line]) -> list[StockAdjustmentItem]:
    return [
        StockAdjustmentItem(
            line_no=index + 1,
            material_id=line.material_id,
            quantity_delta=line.quantity_delta,
            unit_cost=line.unit_cost,
            value_delta=line.value_delta,
            system_quantity=line.system_quantity,
            remarks=line.remarks,
            created_by_id=ctx.user_id,
        )
        for index, line in enumerate(lines)
    ]


async def create(
    session: AsyncSession, ctx: AccessContext, data: AdjustmentCreate
) -> StockAdjustment:
    warehouse = await common.warehouse_in_scope(session, ctx, data.warehouse_id, PERM_ADJUST)
    lines = await _build_lines(session, ctx, warehouse, data)
    adjustment = StockAdjustment(
        company_id=ctx.company_id,
        adjustment_number=await common.next_doc_number(session, ctx, DocumentType.STOCK_ADJUSTMENT),
        status=AdjustmentStatus.DRAFT.value,
        warehouse_id=warehouse.id,
        project_id=warehouse.project_id,
        site_id=warehouse.site_id,
        reason_code=data.reason_code.value,
        reason_note=data.reason_note.strip(),
        adjustment_date=data.adjustment_date or today_utc(),
        created_by_id=ctx.user_id,
        items=_items(ctx, lines),
    )
    session.add(adjustment)
    await session.flush()
    return adjustment


async def update(
    session: AsyncSession,
    ctx: AccessContext,
    adjustment_id: UUID,
    data: AdjustmentCreate,
    *,
    expected_version: int | None,
) -> StockAdjustment:
    adjustment = await _get_for_update(session, ctx, adjustment_id)
    _check_version(adjustment, expected_version)
    _in_scope(ctx, PERM_ADJUST, adjustment)
    if not AdjustmentStatus(adjustment.status).is_editable:
        raise BusinessRuleError(
            "adjustment_not_editable",
            f"{adjustment.adjustment_number} is {common.human(adjustment.status)}; only a draft "
            "or a rejected adjustment can be edited.",
        )
    warehouse = await common.warehouse_in_scope(session, ctx, data.warehouse_id, PERM_ADJUST)
    lines = await _build_lines(session, ctx, warehouse, data)
    adjustment.warehouse_id = warehouse.id
    adjustment.project_id = warehouse.project_id
    adjustment.site_id = warehouse.site_id
    adjustment.reason_code = data.reason_code.value
    adjustment.reason_note = data.reason_note.strip()
    adjustment.adjustment_date = data.adjustment_date or adjustment.adjustment_date
    adjustment.items.clear()
    await session.flush()
    adjustment.items.extend(_items(ctx, lines))
    adjustment.status = AdjustmentStatus.DRAFT.value
    adjustment.decision_reason = None
    adjustment.version += 1
    adjustment.updated_by_id = ctx.user_id
    await session.flush()
    return adjustment


async def cancel(
    session: AsyncSession, ctx: AccessContext, adjustment_id: UUID, reason: str
) -> StockAdjustment:
    adjustment = await _get_for_update(session, ctx, adjustment_id)
    _in_scope(ctx, PERM_ADJUST, adjustment)
    if not AdjustmentStatus(adjustment.status).is_editable:
        raise BusinessRuleError(
            "adjustment_not_cancellable",
            f"{adjustment.adjustment_number} is {common.human(adjustment.status)}. A posted "
            "adjustment is corrected with another adjustment; a pending one is withdrawn first.",
        )
    if len(reason.strip()) < 5:
        raise common.fail("reason", "Say why this adjustment is being cancelled")
    adjustment.status = AdjustmentStatus.CANCELLED.value
    adjustment.cancelled_at = utcnow()
    adjustment.cancel_reason = reason.strip()
    adjustment.version += 1
    adjustment.updated_by_id = ctx.user_id
    await session.flush()
    return adjustment


# -----------------------------------------------------------------------------
# Approval
# -----------------------------------------------------------------------------


def totals(adjustment: StockAdjustment) -> tuple[Decimal, Decimal, Decimal]:
    """(value moved regardless of direction, net value, quantity moved)."""
    zero = Decimal(0)
    value_abs = sum((abs(i.value_delta or zero) for i in adjustment.items), zero)
    value_net = sum((i.value_delta or zero for i in adjustment.items), zero)
    quantity_abs = sum((abs(i.quantity_delta) for i in adjustment.items), zero)
    return value_abs, value_net, quantity_abs


async def submit(
    session: AsyncSession,
    ctx: AccessContext,
    adjustment_id: UUID,
    *,
    expected_version: int | None,
) -> StockAdjustment:
    adjustment = await _get_for_update(session, ctx, adjustment_id)
    _check_version(adjustment, expected_version)
    _in_scope(ctx, PERM_ADJUST, adjustment)
    if not AdjustmentStatus(adjustment.status).is_editable:
        raise BusinessRuleError(
            "adjustment_not_submittable",
            f"{adjustment.adjustment_number} is {common.human(adjustment.status)}; only a draft "
            "or a rejected adjustment can be submitted.",
        )
    if not adjustment.items:
        raise BusinessRuleError("adjustment_empty", "The adjustment has no lines.")

    # Re-check against the stock as it is now: the draft may be days old.
    warehouse = await warehouse_lookup.get(
        session, company_id=adjustment.company_id, warehouse_id=adjustment.warehouse_id
    )
    if warehouse is None:
        raise BusinessRuleError("warehouse_gone", "The warehouse no longer exists.")
    await _recheck_stock(session, adjustment)

    place = await document_lookup.resolve_place(
        session,
        company_id=adjustment.company_id,
        project_id=adjustment.project_id,
        site_id=adjustment.site_id,
    )
    value_abs, value_net, quantity_abs = totals(adjustment)
    subject = approvals.ApprovalSubject(
        doc_type=DOC_TYPE,
        doc_id=adjustment.id,
        company_id=adjustment.company_id,
        initiated_by=ctx.user_id,
        context={
            "value_abs": value_abs,
            "value_net": value_net,
            "quantity_abs": quantity_abs,
            "reason_code": adjustment.reason_code,
            "is_write_off": any(i.quantity_delta < 0 for i in adjustment.items),
            "line_count": len(adjustment.items),
            "warehouse": {"id": warehouse.id, "code": warehouse.code},
            "project": {"id": place.project_id, "code": place.project_code},
            "site": {"id": place.site_id, "code": place.site_code},
            "requester": {"id": ctx.user_id, "roles": sorted(ctx.role_codes)},
        },
        document_hash=_hash(adjustment),
        doc_number=adjustment.adjustment_number,
        summary=f"{adjustment.reason_code.replace('_', ' ').title()} at {warehouse.code}: "
        f"{adjustment.reason_note}",
        amount=value_abs,
        currency_code="PKR",
        link_path=f"/inventory/adjustments/{adjustment.id}",
        project_id=adjustment.project_id,
        site_id=adjustment.site_id,
    )
    # One savepoint: a routing refusal must not leave a pending adjustment
    # behind. A zero-step rule approves inside `approvals.submit`, which posts
    # the lines through the handler below.
    async with session.begin_nested():
        adjustment.status = AdjustmentStatus.PENDING_APPROVAL.value
        adjustment.submitted_at = utcnow()
        adjustment.decision_reason = None
        adjustment.version += 1
        adjustment.updated_by_id = ctx.user_id
        await session.flush()
        request = await approvals.submit(session, subject)
        adjustment.approval_request_id = request.id
        await session.flush()
    return adjustment


async def _recheck_stock(session: AsyncSession, adjustment: StockAdjustment) -> None:
    remaining: dict[UUID, Decimal] = {}
    for item in adjustment.items:
        on_hand, _ = await ledger.position(
            session, warehouse_id=adjustment.warehouse_id, material_id=item.material_id
        )
        available = remaining.get(item.material_id, on_hand)
        if item.quantity_delta < 0 and -item.quantity_delta > available:
            raise BusinessRuleError(
                "insufficient_stock",
                f"Line {item.line_no} takes out {-item.quantity_delta:f}, but only "
                f"{available:f} is in stock now. Edit the adjustment to the current figure.",
            )
        remaining[item.material_id] = available + item.quantity_delta


async def withdraw(
    session: AsyncSession, ctx: AccessContext, adjustment_id: UUID, reason: str | None
) -> StockAdjustment:
    """Take a pending adjustment back to a draft (only its submitter, and only
    while nobody has approved a step)."""
    adjustment = await _get_for_update(session, ctx, adjustment_id)
    _in_scope(ctx, PERM_ADJUST, adjustment)
    if (
        adjustment.status != AdjustmentStatus.PENDING_APPROVAL.value
        or adjustment.approval_request_id is None
    ):
        raise BusinessRuleError(
            "adjustment_not_pending",
            f"{adjustment.adjustment_number} is {common.human(adjustment.status)}; "
            "only a pending adjustment can be withdrawn.",
        )
    await approvals.recall(session, ctx, adjustment.approval_request_id, reason)
    await session.refresh(adjustment)
    return adjustment


def _hash(adjustment: StockAdjustment) -> str:
    return approvals.document_hash(
        {
            "warehouse_id": adjustment.warehouse_id,
            "reason_code": adjustment.reason_code,
            "reason_note": adjustment.reason_note,
            "date": adjustment.adjustment_date,
            "lines": [
                {
                    "material_id": i.material_id,
                    "delta": i.quantity_delta,
                    "unit_cost": i.unit_cost,
                }
                for i in adjustment.items
            ],
        }
    )


async def _post_lines(
    session: AsyncSession, adjustment: StockAdjustment, actor_id: UUID | None
) -> None:
    """Move the stock. Runs inside the approval's transaction, as whoever
    decided it (or as the submitter when a rule approved it by itself)."""
    ctx = system_access_context(
        adjustment.company_id, actor_id or adjustment.created_by_id or UUID(int=0)
    )
    # Lock in a fixed order so two adjustments cannot deadlock on each other.
    for item in sorted(adjustment.items, key=lambda i: (str(i.material_id), i.line_no)):
        delta = item.quantity_delta
        txn = await ledger.post(
            session,
            ctx,
            ledger.MovementRequest(
                txn_type=TxnType.ADJUST_IN if delta > 0 else TxnType.ADJUST_OUT,
                warehouse_id=adjustment.warehouse_id,
                material_id=item.material_id,
                quantity=abs(delta),
                unit_cost=item.unit_cost if delta > 0 else None,
                source_type="STOCK_ADJUSTMENT",
                source_id=adjustment.id,
                source_line_id=item.id,
                transaction_date=adjustment.adjustment_date,
                remarks=f"{adjustment.adjustment_number}: {adjustment.reason_code}",
            ),
        )
        item.inventory_txn_id = txn.id
        # What it was really worth: an outgoing line is valued at the average
        # cost of the moment it was posted.
        item.unit_cost = txn.unit_cost
        item.value_delta = txn.value_in if delta > 0 else -txn.value_out

    await _post_adjustment_to_gl(session, ctx, adjustment)


async def _post_adjustment_to_gl(
    session: AsyncSession, ctx: AccessContext, adjustment: StockAdjustment
) -> None:
    """The books catching up with what was actually on the shelf is charged
    or credited to site overheads, not to inventory's own value (docs/10:
    inventory auto-posting) — increases and decreases use different accounts,
    so each direction present becomes its own balanced pair of lines in one
    entry."""
    increased = sum(
        (i.value_delta for i in adjustment.items if i.value_delta and i.value_delta > 0), Decimal(0)
    )
    decreased = -sum(
        (i.value_delta for i in adjustment.items if i.value_delta and i.value_delta < 0), Decimal(0)
    )
    dims = {"project_id": adjustment.project_id, "site_id": adjustment.site_id}
    lines = []
    for amount, direction, what in (
        (increased, "increase", "a stock adjustment that finds more than the books said"),
        (decreased, "decrease", "a stock adjustment that finds less than the books said"),
    ):
        if amount <= 0:
            continue
        rule = await posting_rules.resolve_or_fail(
            session,
            ctx,
            source_type=JournalSourceType.INVENTORY,
            event="ADJUSTMENT",
            context={"direction": direction},
            what=what,
        )
        lines.append(gl.debit(rule.debit_account_id, amount, **dims))
        lines.append(gl.credit(rule.credit_account_id, amount, **dims))
    if not lines:
        return
    entry = await gl.post_system_entry(
        session,
        ctx,
        source_type=JournalSourceType.INVENTORY,
        source_id=adjustment.id,
        entry_date=adjustment.adjustment_date,
        description=f"Stock adjustment: {adjustment.adjustment_number} ({adjustment.reason_code})",
        reference=None,
        lines=lines,
    )
    adjustment.journal_entry_id = entry.id


class StockAdjustmentApprovals:
    spec = registry.DocumentTypeSpec(
        doc_type=DOC_TYPE,
        label="Stock adjustment",
        approve_permission=PERM_APPROVE,
        context_variables=CONTEXT_VARIABLES,
    )

    async def _load(self, session: AsyncSession, doc_id: UUID) -> StockAdjustment:
        row = (
            await session.execute(select(StockAdjustment).where(StockAdjustment.id == doc_id))
        ).scalar_one()
        await session.refresh(row, attribute_names=["items"])
        return row

    async def current_hash(self, session: AsyncSession, doc_id: UUID) -> str | None:
        return _hash(await self._load(session, doc_id))

    async def pending_document_ids(self, session: AsyncSession) -> dict[UUID, UUID]:
        rows = await session.execute(
            select(StockAdjustment.id, StockAdjustment.company_id).where(
                StockAdjustment.status == AdjustmentStatus.PENDING_APPROVAL.value
            )
        )
        return dict(rows.tuples().all())

    async def on_approved(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        adjustment = await self._load(session, outcome.doc_id)
        await _post_lines(session, adjustment, outcome.actor_user_id)
        # Set here as well as by `submit`: a rule that approves by itself calls
        # this before `submit` has stored the request id, and a posted
        # adjustment must always name the approval it went through.
        adjustment.approval_request_id = outcome.request_id
        adjustment.status = AdjustmentStatus.POSTED.value
        adjustment.posted_at = utcnow()
        adjustment.posted_by_id = outcome.actor_user_id
        adjustment.decision_reason = outcome.reason
        adjustment.version += 1
        await session.flush()

    async def _back(
        self,
        session: AsyncSession,
        outcome: registry.ApprovalOutcome,
        status: AdjustmentStatus,
    ) -> None:
        adjustment = await self._load(session, outcome.doc_id)
        adjustment.status = status.value
        adjustment.decision_reason = outcome.reason
        adjustment.version += 1
        await session.flush()

    async def on_rejected(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._back(session, outcome, AdjustmentStatus.REJECTED)

    async def on_changes_requested(
        self, session: AsyncSession, outcome: registry.ApprovalOutcome
    ) -> None:
        await self._back(session, outcome, AdjustmentStatus.DRAFT)

    async def on_recalled(self, session: AsyncSession, outcome: registry.ApprovalOutcome) -> None:
        await self._back(session, outcome, AdjustmentStatus.DRAFT)


registry.register(StockAdjustmentApprovals())


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


async def list_adjustments(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[StockAdjustment], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, adjustment_id: UUID) -> StockAdjustment:
    return await repository(session).get(ctx, PERM_VIEW, adjustment_id)
