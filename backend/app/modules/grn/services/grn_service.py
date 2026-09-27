"""Goods received notes: raise from a delivery, inspect, post, cancel.

Posting is the moment stock moves (docs/02 §6): each accepted line goes into
the inventory ledger at its priced cost, the order line records what was
received and accepted, and the delivery is marked received. All in one
transaction — a GRN either did all of that or none of it.

Cancelling a posted GRN never deletes anything: it writes contra rows to the
ledger, gives the quantities back to the order, and returns the delivery to
"approved" so it can be received again.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.errors import (
    BusinessRuleError,
    ConversionNotConfiguredError,
    ValidationError,
    VersionConflictError,
)
from app.core.pagination import PageParams
from app.core.scoping import assert_in_scope
from app.core.types import utcnow
from app.modules.deliveries.services import receipts
from app.modules.finance.domain.enums import CommitmentSourceType, JournalSourceType
from app.modules.finance.services import budgets, posting_rules
from app.modules.finance.services import ledger as gl
from app.modules.grn.domain.enums import GrnStatus, InspectionResult
from app.modules.grn.models import Grn, GrnItem
from app.modules.grn.schemas import CounterPurchaseCreate
from app.modules.inventory.domain.enums import TxnType
from app.modules.inventory.services import ledger
from app.modules.masterdata.services import material_lookup, warehouse_lookup
from app.modules.masterdata.services.conversion import UnitConverter
from app.modules.org.services import company_service
from app.modules.procurement.services import po_lookup, receiving
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.services import approval_limit
from app.modules.vendors.services import vendor_lookup
from app.platform.numbering import DocumentType, next_number

PERM_VIEW = "grn.view"
PERM_CREATE = "grn.create"
PERM_APPROVE = "grn.approve"
PERM_CANCEL = "grn.cancel"

_AMOUNT = Decimal("0.0001")
_COST = Decimal("0.000001")


def repository(session: AsyncSession) -> ScopedRepository[Grn]:
    return ScopedRepository(
        session,
        Grn,
        entity_name="Goods received note",
        sortable={
            "grn_number",
            "status",
            "received_date",
            "net_amount",
            "created_at",
            "updated_at",
        },
        searchable=("grn_number",),
        default_sort="-created_at",
    )


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


def _human(status: str) -> str:
    return status.lower().replace("_", " ")


async def _get_for_update(
    session: AsyncSession, ctx: AccessContext, grn_id: UUID, perm: str
) -> Grn:
    grn = await repository(session).get_for_update(ctx, perm, grn_id)
    await session.refresh(grn, attribute_names=["items"])
    return grn


def _check_version(grn: Grn, expected: int | None) -> None:
    if expected is not None and grn.version != expected:
        raise VersionConflictError(
            f"{grn.grn_number} was changed by someone else (version {grn.version}, "
            f"you had {expected}). Reload and try again."
        )


# -----------------------------------------------------------------------------
# Raise from a delivery
# -----------------------------------------------------------------------------


async def create_from_delivery(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID, warehouse_id: UUID | None
) -> Grn:
    delivery = await receipts.get_receivable(session, ctx, delivery_id)
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=ctx.company_id,
        project_id=delivery.project_id,
        site_id=delivery.site_id,
        entity="Delivery",
    )
    if delivery.status != "APPROVED":
        raise BusinessRuleError(
            "delivery_not_approved",
            f"{delivery.delivery_number} is {_human(delivery.status)}; only an approved delivery "
            "can be received into stock.",
        )
    if delivery.grn_id is not None:
        raise BusinessRuleError(
            "delivery_already_received", f"{delivery.delivery_number} already has a GRN."
        )

    if warehouse_id is None:
        warehouse = await warehouse_lookup.default_receiving(
            session, company_id=ctx.company_id, site_id=delivery.site_id
        )
        if warehouse is None:
            raise BusinessRuleError(
                "no_receiving_warehouse",
                "This site has no default receiving warehouse. Choose one, or mark one as the "
                "site's default in the warehouse list.",
            )
    else:
        warehouse = await warehouse_lookup.get(
            session, company_id=ctx.company_id, warehouse_id=warehouse_id
        )
        if warehouse is None or warehouse.site_id != delivery.site_id:
            raise _fail("warehouse_id", "The warehouse must belong to the delivery's site")

    number = await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DocumentType.GRN,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )
    ordered = await receipts.ordered_quantities(session, [i.po_item_id for i in delivery.items])
    grn = Grn(
        company_id=ctx.company_id,
        grn_number=number,
        status=GrnStatus.DRAFT.value,
        delivery_id=delivery.id,
        purchase_order_id=delivery.purchase_order_id,
        vendor_id=delivery.vendor_id,
        project_id=delivery.project_id,
        site_id=delivery.site_id,
        warehouse_id=warehouse.id,
        received_date=delivery.captured_at.date(),
        inspection_result=InspectionResult.PENDING.value,
        gross_amount=Decimal(0),
        tax_amount=Decimal(0),
        net_amount=Decimal(0),
        created_by_id=ctx.user_id,
        items=[
            GrnItem(
                line_no=index + 1,
                po_item_id=item.po_item_id,
                delivery_item_id=item.id,
                material_id=item.material_id,
                unit_id=item.unit_id,
                ordered_quantity=ordered.get(item.po_item_id) if item.po_item_id else None,
                delivered_quantity=item.quantity,
                # Everything that arrived is accepted until inspection says otherwise.
                accepted_quantity=item.quantity,
                rejected_quantity=Decimal(0),
                rate=item.rate,
                vendor_rate_id=item.vendor_rate_id,
                amount=item.amount,
                created_by_id=ctx.user_id,
            )
            for index, item in enumerate(delivery.items)
        ],
    )
    _sum(grn)
    session.add(grn)
    await session.flush()
    await receipts.link_grn(session, delivery.id, grn.id)
    return grn


PURCHASE_LIMIT_DOC = "counter_purchase"


async def _next_grn_number(session: AsyncSession, ctx: AccessContext) -> str:
    return await next_number(
        session,
        company_id=ctx.company_id,
        doc_type=DocumentType.GRN,
        fiscal_year_start_month=await company_service.fiscal_year_start_month(
            session, ctx.company_id
        ),
    )


async def create_counter_purchase(
    session: AsyncSession, ctx: AccessContext, data: CounterPurchaseCreate
) -> Grn:
    """Stock bought over the counter, with no delivery and no order behind it.

    The riskiest way stock arrives, so it asks for what makes it checkable: a bill
    or receipt number (entered once per vendor), a price per line from that bill,
    and, if a `PURCHASE_LIMIT` rule applies to the person, a value within it.
    Otherwise it is an ordinary GRN: drafted, inspected, and posted by someone
    with the right to post, which is when stock moves.
    """
    warehouse = await warehouse_lookup.get(
        session, company_id=ctx.company_id, warehouse_id=data.warehouse_id
    )
    if warehouse is None:
        raise _fail("warehouse_id", "Unknown warehouse")
    assert_in_scope(
        ctx,
        PERM_CREATE,
        company_id=ctx.company_id,
        project_id=warehouse.project_id,
        site_id=warehouse.site_id,
        entity="Warehouse",
    )
    vendor = (
        await vendor_lookup.vendors(session, company_id=ctx.company_id, vendor_ids={data.vendor_id})
    ).get(data.vendor_id)
    if vendor is None:
        raise _fail("vendor_id", "Unknown vendor")
    if not vendor.can_receive_orders:
        raise BusinessRuleError(
            "vendor_not_usable",
            f"{vendor.name} is {_human(vendor.status)}; nothing can be bought from them.",
        )

    materials = await material_lookup.materials(
        session, company_id=ctx.company_id, material_ids={i.material_id for i in data.lines}
    )
    errors: list[dict[str, str]] = []
    for index, line in enumerate(data.lines):
        material = materials.get(line.material_id)
        if material is None or not material.is_stockable:
            errors.append(
                {
                    "field": f"lines.{index}.material_id",
                    "code": "invalid",
                    "message": "unknown, or not a material held in stock",
                }
            )
    if errors:
        raise ValidationError("Some lines are not valid.", errors=errors)

    reference = data.reference.strip()
    clash = await session.scalar(
        select(func.count())
        .select_from(Grn)
        .where(
            Grn.company_id == ctx.company_id,
            Grn.vendor_id == vendor.id,
            Grn.counter_reference == reference,
            Grn.status != GrnStatus.CANCELLED.value,
        )
    )
    if clash:
        raise BusinessRuleError(
            "duplicate_reference",
            f"Bill {reference} from {vendor.name} has already been received.",
        )

    amounts = [
        (line.rate * line.quantity).quantize(_AMOUNT, rounding=ROUND_HALF_UP) for line in data.lines
    ]
    total = sum(amounts, Decimal(0))
    limit = await approval_limit.check(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        doc_type=PURCHASE_LIMIT_DOC,
        approve_permission=PERM_CREATE,
        amount=total,
        project_id=warehouse.project_id,
        site_id=warehouse.site_id,
        rule_type=RuleType.PURCHASE_LIMIT,
    )
    if not limit.allowed:
        raise BusinessRuleError(
            "purchase_limit_exceeded",
            limit.message("counter purchase", total, noun="purchase limit")
            + " Raise a purchase order instead.",
        )

    grn = Grn(
        company_id=ctx.company_id,
        grn_number=await _next_grn_number(session, ctx),
        status=GrnStatus.DRAFT.value,
        delivery_id=None,
        purchase_order_id=None,
        vendor_id=vendor.id,
        project_id=warehouse.project_id,
        site_id=warehouse.site_id,
        warehouse_id=warehouse.id,
        received_date=data.received_date or utcnow().date(),
        counter_reference=reference,
        inspection_result=InspectionResult.PENDING.value,
        gross_amount=Decimal(0),
        tax_amount=Decimal(0),
        net_amount=Decimal(0),
        remarks=data.remarks,
        created_by_id=ctx.user_id,
        items=[
            GrnItem(
                line_no=index + 1,
                material_id=line.material_id,
                unit_id=line.unit_id,
                delivered_quantity=line.quantity,
                accepted_quantity=line.quantity,
                rejected_quantity=Decimal(0),
                rate=line.rate,
                amount=amounts[index],
                batch_no=line.batch_no,
                created_by_id=ctx.user_id,
            )
            for index, line in enumerate(data.lines)
        ],
    )
    _sum(grn)
    session.add(grn)
    await session.flush()
    return grn


def _sum(grn: Grn) -> None:
    grn.gross_amount = sum((i.amount or Decimal(0) for i in grn.items), Decimal(0))
    grn.net_amount = grn.gross_amount + grn.tax_amount


# -----------------------------------------------------------------------------
# Inspection
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineInspection:
    grn_item_id: UUID
    accepted_quantity: Decimal
    rejection_reason: str | None = None
    batch_no: str | None = None
    expiry_date: date | None = None


async def inspect(
    session: AsyncSession,
    ctx: AccessContext,
    grn_id: UUID,
    lines: list[LineInspection],
    *,
    warehouse_id: UUID | None,
    remarks: str | None,
    expected_version: int | None,
) -> Grn:
    """Record what inspection found: how much of each line is taken. What is
    not taken is rejected, and must say why."""
    grn = await _get_for_update(session, ctx, grn_id, PERM_CREATE)
    _check_version(grn, expected_version)
    _in_scope(ctx, PERM_CREATE, grn)
    if grn.status != GrnStatus.DRAFT.value:
        raise BusinessRuleError(
            "grn_not_draft",
            f"{grn.grn_number} is {_human(grn.status)}; only a draft can be inspected.",
        )
    by_id = {i.id: i for i in grn.items}
    errors: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        item = by_id.get(line.grn_item_id)
        if item is None:
            errors.append(
                {
                    "field": f"lines.{index}.grn_item_id",
                    "code": "invalid",
                    "message": "not on this GRN",
                }
            )
            continue
        if line.accepted_quantity < 0 or line.accepted_quantity > item.delivered_quantity:
            errors.append(
                {
                    "field": f"lines.{index}.accepted_quantity",
                    "code": "invalid",
                    "message": f"between 0 and the {item.delivered_quantity:f} delivered",
                }
            )
            continue
        rejected = item.delivered_quantity - line.accepted_quantity
        if rejected > 0 and not (line.rejection_reason or "").strip():
            errors.append(
                {
                    "field": f"lines.{index}.rejection_reason",
                    "code": "required",
                    "message": "say why the rest was turned away",
                }
            )
            continue
        item.accepted_quantity = line.accepted_quantity
        item.rejected_quantity = rejected
        item.rejection_reason = (line.rejection_reason or "").strip() or None
        item.batch_no = line.batch_no
        item.expiry_date = line.expiry_date
        # Price follows what is accepted. A delivery's load was priced whole, so it is
        # scaled; a counter purchase has no delivery, so its bill's rate does the work.
        if item.delivery_item_id is None:
            item.amount = (
                None
                if item.rate is None
                else (item.rate * item.accepted_quantity).quantize(_AMOUNT, rounding=ROUND_HALF_UP)
            )
        else:
            source = await receipts.item_amount(session, item.delivery_item_id)
            item.amount = (
                None
                if source is None
                else (source * item.accepted_quantity / item.delivered_quantity).quantize(
                    _AMOUNT, rounding=ROUND_HALF_UP
                )
            )
    if errors:
        raise ValidationError("The inspection is not valid.", errors=errors)

    if warehouse_id is not None and warehouse_id != grn.warehouse_id:
        warehouse = await warehouse_lookup.get(
            session, company_id=ctx.company_id, warehouse_id=warehouse_id
        )
        if warehouse is None or warehouse.site_id != grn.site_id:
            raise _fail("warehouse_id", "The warehouse must belong to the delivery's site")
        grn.warehouse_id = warehouse.id
    if remarks is not None:
        grn.remarks = remarks
    accepted = [i.accepted_quantity for i in grn.items]
    grn.inspection_result = (
        InspectionResult.FAILED
        if all(q == 0 for q in accepted)
        else InspectionResult.PASSED
        if all(i.rejected_quantity == 0 for i in grn.items)
        else InspectionResult.PARTIAL
    ).value
    grn.inspected_by_id = ctx.user_id
    _sum(grn)
    grn.updated_by_id = ctx.user_id
    grn.version += 1
    await session.flush()
    return grn


def _in_scope(ctx: AccessContext, perm: str, grn: Grn) -> None:
    assert_in_scope(
        ctx,
        perm,
        company_id=grn.company_id,
        project_id=grn.project_id,
        site_id=grn.site_id,
        entity="Goods received note",
    )


async def reprice(session: AsyncSession, ctx: AccessContext, grn_id: UUID) -> Grn:
    """Price the lines that arrived unpriced, once a rate has been set.

    A delivery is captured now and priced later; this is "later". The rate is
    resolved as of the capture date, exactly as ingest would have, and the price
    is written back to the delivery line too, so the two never disagree.
    """
    grn = await _get_for_update(session, ctx, grn_id, PERM_CREATE)
    _in_scope(ctx, PERM_CREATE, grn)
    if grn.status != GrnStatus.DRAFT.value:
        raise BusinessRuleError("grn_not_draft", "Only a draft GRN can be repriced.")
    converter = UnitConverter(session, ctx.company_id)
    for item in grn.items:
        if item.amount is not None or item.accepted_quantity == 0:
            continue
        priced = await receipts.price_item(
            session,
            ctx,
            converter,
            delivery_item_id=item.delivery_item_id,
            vendor_id=grn.vendor_id,
            project_id=grn.project_id,
            site_id=grn.site_id,
            captured_on=grn.received_date,
        )
        if priced is None:
            continue
        item.rate = priced.rate
        item.vendor_rate_id = priced.vendor_rate_id
        item.amount = (priced.amount * item.accepted_quantity / item.delivered_quantity).quantize(
            _AMOUNT, rounding=ROUND_HALF_UP
        )
    _sum(grn)
    grn.version += 1
    await session.flush()
    return grn


# -----------------------------------------------------------------------------
# Post
# -----------------------------------------------------------------------------


async def post(
    session: AsyncSession, ctx: AccessContext, grn_id: UUID, *, expected_version: int | None
) -> Grn:
    grn = await _get_for_update(session, ctx, grn_id, PERM_APPROVE)
    _check_version(grn, expected_version)
    _in_scope(ctx, PERM_APPROVE, grn)
    if grn.status != GrnStatus.DRAFT.value:
        raise BusinessRuleError(
            "grn_not_draft", f"{grn.grn_number} is {_human(grn.status)}; it cannot be posted."
        )
    unpriced = [i.line_no for i in grn.items if i.accepted_quantity > 0 and i.amount is None]
    if unpriced:
        # Stock is valued at what it cost. Posting an unpriced line would put
        # goods on the shelf at nothing and drag the average cost down.
        raise BusinessRuleError(
            "grn_unpriced",
            f"Line(s) {', '.join(map(str, unpriced))} have no price yet. Set the vendor's rate "
            "and reprice the GRN, then post it.",
        )
    if grn.inspection_result == InspectionResult.PENDING.value:
        grn.inspection_result = InspectionResult.PASSED.value
        grn.inspected_by_id = ctx.user_id

    delivery = (
        await receipts.get_receivable(session, ctx, grn.delivery_id) if grn.delivery_id else None
    )
    converter = UnitConverter(session, ctx.company_id)
    on = grn.received_date
    any_short = False

    for item in grn.items:
        if item.rejected_quantity > 0:
            any_short = True
        if item.accepted_quantity == 0:
            continue
        material = (
            await material_lookup.materials(
                session, company_id=ctx.company_id, material_ids={item.material_id}
            )
        )[item.material_id]
        if material.is_stockable:
            try:
                base = await converter.convert(
                    item.accepted_quantity,
                    item.unit_id,
                    material.base_unit_id,
                    material_id=item.material_id,
                    vendor_id=grn.vendor_id,
                    at=on,
                )
            except ConversionNotConfiguredError as exc:
                raise BusinessRuleError(
                    "grn_conversion_missing",
                    f"{exc}. Set up the conversion factor before posting.",
                ) from exc
            item.base_quantity = base.converted_quantity
            assert item.amount is not None
            item.unit_cost = (item.amount / base.converted_quantity).quantize(
                _COST, rounding=ROUND_HALF_UP
            )
            txn = await ledger.post(
                session,
                ctx,
                ledger.MovementRequest(
                    txn_type=TxnType.GRN_IN,
                    warehouse_id=grn.warehouse_id,
                    material_id=item.material_id,
                    quantity=base.converted_quantity,
                    unit_cost=item.unit_cost,
                    source_type="GRN",
                    source_id=grn.id,
                    source_line_id=item.id,
                    transaction_date=grn.received_date,
                    remarks=grn.grn_number,
                ),
            )
            item.inventory_txn_id = txn.id
        if item.po_item_id is not None:
            await _apply_receipt(session, converter, grn, item, sign=1)

    po_info = (
        await po_lookup.order(
            session, company_id=ctx.company_id, purchase_order_id=grn.purchase_order_id
        )
        if grn.purchase_order_id
        else None
    )
    await _post_gl_and_budget(session, ctx, grn, po_info)

    grn.status = GrnStatus.POSTED.value
    grn.posted_at = utcnow()
    grn.posted_by_id = ctx.user_id
    grn.version += 1
    await session.flush()
    if delivery is not None:
        await receipts.mark_received(session, delivery.id, partial=any_short)
    return grn


async def _post_gl_and_budget(
    session: AsyncSession, ctx: AccessContext, grn: Grn, po_info: po_lookup.OrderInfo | None
) -> None:
    """The GL side of a receipt, and the budget it counts against (docs/07
    §3): every accepted, priced line resolves — through the same posting rule,
    whether it is destined for the ledger or for a budget line — to a debit
    and a credit account. A GRN with no priced, accepted lines (nothing
    happened) posts nothing."""
    debit_totals: dict[UUID, Decimal] = defaultdict(Decimal)
    credit_totals: dict[UUID, Decimal] = defaultdict(Decimal)
    commitment_lines: list[budgets.CommitmentLine] = []
    for item in grn.items:
        if item.accepted_quantity == 0 or item.amount is None:
            continue
        is_po_backed = item.po_item_id is not None
        rule = await posting_rules.resolve_receipt_account(
            session, ctx, material_id=item.material_id, is_po_backed=is_po_backed
        )
        debit_totals[rule.debit_account_id] += item.amount
        credit_totals[rule.credit_account_id] += item.amount
        commitment_lines.append(
            budgets.CommitmentLine(
                material_id=item.material_id, amount=item.amount, is_po_backed=is_po_backed
            )
        )
    if not debit_totals:
        return

    phase_id = po_info.phase_id if po_info else None
    cost_center_id = po_info.cost_center_id if po_info else None
    dims = {
        "project_id": grn.project_id,
        "site_id": grn.site_id,
        "phase_id": phase_id,
        "cost_center_id": cost_center_id,
    }
    lines = [gl.debit(account_id, amount, **dims) for account_id, amount in debit_totals.items()]
    lines += [gl.credit(account_id, amount, **dims) for account_id, amount in credit_totals.items()]
    await gl.post_system_entry(
        session,
        ctx,
        source_type=JournalSourceType.GRN,
        source_id=grn.id,
        entry_date=grn.received_date,
        description=f"Goods received: {grn.grn_number}",
        reference=grn.counter_reference,
        lines=lines,
    )
    if grn.project_id is None:
        return  # nothing to measure the spend against
    await budgets.release_receipt(
        session,
        ctx,
        source_type=CommitmentSourceType.PO,
        source_id=grn.purchase_order_id,
        project_id=grn.project_id,
        phase_id=phase_id,
        cost_center_id=cost_center_id,
        on=grn.received_date,
        lines=commitment_lines,
    )


async def _apply_receipt(
    session: AsyncSession, converter: UnitConverter, grn: Grn, item: GrnItem, *, sign: int
) -> None:
    """Tell the order what arrived, in the order line's own unit."""
    assert item.po_item_id is not None
    po_unit = await receipts.order_item_unit(session, item.po_item_id)
    if po_unit is None:
        return

    async def convert(quantity: Decimal) -> Decimal:
        if po_unit == item.unit_id:
            return quantity
        return (
            await converter.convert(
                quantity,
                item.unit_id,
                po_unit,
                material_id=item.material_id,
                vendor_id=grn.vendor_id,
                at=grn.received_date,
            )
        ).converted_quantity

    try:
        received = await convert(item.delivered_quantity)
        accepted = await convert(item.accepted_quantity)
    except ConversionNotConfiguredError:
        return  # the order cannot be updated in a unit that cannot be converted to
    await receiving.apply_receipt(
        session,
        po_item_id=item.po_item_id,
        received=received * sign,
        accepted=accepted * sign,
    )


# -----------------------------------------------------------------------------
# Cancel
# -----------------------------------------------------------------------------


async def cancel(session: AsyncSession, ctx: AccessContext, grn_id: UUID, reason: str) -> Grn:
    grn = await _get_for_update(session, ctx, grn_id, PERM_CANCEL)
    _in_scope(ctx, PERM_CANCEL, grn)
    if grn.status == GrnStatus.CANCELLED.value:
        raise BusinessRuleError("grn_cancelled", f"{grn.grn_number} is already cancelled.")
    if len(reason.strip()) < 5:
        raise _fail("reason", "Say why this GRN is being cancelled")

    was_posted = grn.status == GrnStatus.POSTED.value
    converter = UnitConverter(session, ctx.company_id)
    if was_posted:
        # Undo in the reverse of the order it was done in.
        for item in sorted(grn.items, key=lambda i: i.line_no, reverse=True):
            if item.inventory_txn_id is not None:
                await ledger.reverse_by_id(
                    session,
                    ctx,
                    item.inventory_txn_id,
                    source_type="GRN_CANCEL",
                    source_id=grn.id,
                    remarks=f"{grn.grn_number} cancelled",
                )
            if item.po_item_id is not None and item.accepted_quantity + item.rejected_quantity > 0:
                await _apply_receipt(session, converter, grn, item, sign=-1)

    grn.status = GrnStatus.CANCELLED.value
    grn.cancelled_at = utcnow()
    grn.cancel_reason = reason.strip()
    grn.version += 1
    await session.flush()
    if grn.delivery_id is not None:
        await receipts.unlink_grn(session, grn.delivery_id)
    return grn


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


async def list_grns(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    search: str | None,
    filters: dict[str, Any],
) -> tuple[list[Grn], int]:
    return await repository(session).list(ctx, PERM_VIEW, page=page, search=search, filters=filters)


async def get(session: AsyncSession, ctx: AccessContext, grn_id: UUID) -> Grn:
    return await repository(session).get(ctx, PERM_VIEW, grn_id)


_ = func  # imported for query helpers used by the API layer
