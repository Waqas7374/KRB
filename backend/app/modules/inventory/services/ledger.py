"""Post movements to the stock ledger (docs/02 §7).

Every stock change in the system goes through `post`: a GRN, an issue, a
transfer, an adjustment, a reversal. That is what makes the ledger trustworthy
— there is exactly one way in, and it does, in one transaction:

  1. lock the (warehouse, material) balance row,
  2. refuse to go below zero,
  3. work out the weighted-average cost of the movement,
  4. append the ledger row (with the balance *after*),
  5. update the cached balance.

Ledger rows are never updated or deleted; a mistake is put right with a
reversal, a contra row at the original row's own cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.types import utcnow, uuid7
from app.modules.inventory.domain import costing
from app.modules.inventory.domain.enums import TxnType
from app.modules.inventory.models import InventoryBalance, InventoryTransaction
from app.modules.masterdata.services import material_lookup, warehouse_lookup


@dataclass(frozen=True, slots=True)
class MovementRequest:
    txn_type: TxnType
    warehouse_id: UUID
    material_id: UUID
    # In the material's base unit: the ledger has one unit per material.
    quantity: Decimal
    source_type: str
    source_id: UUID
    # Required for an inbound movement; an outbound one leaves at the current
    # average unless a cost is given (a reversal).
    unit_cost: Decimal | None = None
    source_line_id: UUID | None = None
    transaction_date: date | None = None
    remarks: str | None = None
    reversal_of_id: UUID | None = None


def _fail(field: str, message: str) -> ValidationError:
    return ValidationError(
        message, errors=[{"field": field, "code": "invalid", "message": message}]
    )


async def _locked_balance(
    session: AsyncSession,
    ctx: AccessContext,
    warehouse: warehouse_lookup.WarehouseInfo,
    material_id: UUID,
) -> InventoryBalance:
    """Create the balance row if this is the first movement, then lock it. The
    insert ignores a concurrent creator, so two first receipts cannot collide."""
    await session.execute(
        insert(InventoryBalance)
        .values(
            id=uuid7(),
            company_id=ctx.company_id,
            warehouse_id=warehouse.id,
            material_id=material_id,
            project_id=warehouse.project_id,
            site_id=warehouse.site_id,
            quantity_on_hand=0,
            quantity_reserved=0,
            quantity_in_transit=0,
            average_cost=0,
            total_value=0,
        )
        .on_conflict_do_nothing(constraint="uq_inventory_balances_stock")
    )
    return (
        await session.execute(
            select(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == warehouse.id,
                InventoryBalance.material_id == material_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def post(
    session: AsyncSession, ctx: AccessContext, request: MovementRequest
) -> InventoryTransaction:
    if request.quantity <= 0:
        raise _fail("quantity", "A stock movement must be for a positive quantity")
    material = (
        await material_lookup.materials(
            session, company_id=ctx.company_id, material_ids={request.material_id}
        )
    ).get(request.material_id)
    if material is None:
        raise _fail("material_id", "Unknown material")
    if not material.is_stockable:
        raise BusinessRuleError(
            "material_not_stockable",
            f"{material.sku} is not held in stock; it has no stock ledger.",
        )
    warehouse = await warehouse_lookup.get(
        session, company_id=ctx.company_id, warehouse_id=request.warehouse_id
    )
    if warehouse is None:
        raise _fail("warehouse_id", "Unknown warehouse")

    balance = await _locked_balance(session, ctx, warehouse, request.material_id)
    before = costing.Position(balance.quantity_on_hand, balance.total_value, balance.average_cost)

    if request.txn_type.is_inbound:
        if request.unit_cost is None:
            raise _fail("unit_cost", "A receipt needs the cost per unit")
        move = costing.receive(before, request.quantity, request.unit_cost)
        quantity_in, quantity_out = request.quantity, Decimal(0)
        value_in, value_out = move.value, Decimal(0)
    else:
        if request.quantity > balance.quantity_on_hand:
            raise BusinessRuleError(
                "insufficient_stock",
                f"Only {balance.quantity_on_hand:f} of {material.sku} is in {warehouse.code}; "
                f"{request.quantity:f} was asked for.",
            )
        move = costing.issue(before, request.quantity, request.unit_cost)
        quantity_in, quantity_out = Decimal(0), request.quantity
        value_in, value_out = Decimal(0), move.value

    now = utcnow()
    txn = InventoryTransaction(
        company_id=ctx.company_id,
        warehouse_id=warehouse.id,
        material_id=request.material_id,
        txn_type=request.txn_type.value,
        unit_id=material.base_unit_id,
        quantity_in=quantity_in,
        quantity_out=quantity_out,
        unit_cost=move.unit_cost,
        value_in=value_in,
        value_out=value_out,
        balance_quantity_after=move.after.quantity,
        balance_value_after=move.after.value,
        source_type=request.source_type,
        source_id=request.source_id,
        source_line_id=request.source_line_id,
        project_id=warehouse.project_id,
        site_id=warehouse.site_id,
        transaction_date=request.transaction_date or now.date(),
        posted_at=now,
        reversal_of_id=request.reversal_of_id,
        remarks=request.remarks,
        created_by_id=ctx.user_id,
    )
    session.add(txn)
    await session.flush()

    balance.quantity_on_hand = move.after.quantity
    balance.total_value = move.after.value
    balance.average_cost = move.after.average_cost
    balance.last_transaction_id = txn.id
    balance.last_movement_at = now
    await session.flush()
    return txn


async def reverse(
    session: AsyncSession,
    ctx: AccessContext,
    original: InventoryTransaction,
    *,
    source_type: str,
    source_id: UUID,
    remarks: str,
) -> InventoryTransaction:
    """Undo a ledger row with a contra row at the original's own cost."""
    inbound = original.quantity_in > 0
    return await post(
        session,
        ctx,
        MovementRequest(
            txn_type=TxnType.REVERSAL_OUT if inbound else TxnType.REVERSAL_IN,
            warehouse_id=original.warehouse_id,
            material_id=original.material_id,
            quantity=original.quantity_in if inbound else original.quantity_out,
            unit_cost=original.unit_cost,
            source_type=source_type,
            source_id=source_id,
            source_line_id=original.source_line_id,
            remarks=remarks,
            reversal_of_id=original.id,
        ),
    )


async def reverse_by_id(
    session: AsyncSession,
    ctx: AccessContext,
    transaction_id: UUID,
    *,
    source_type: str,
    source_id: UUID,
    remarks: str,
) -> InventoryTransaction:
    original = await session.get(InventoryTransaction, transaction_id)
    if original is None or original.company_id != ctx.company_id:
        raise NotFoundError("Stock movement", transaction_id)
    return await reverse(
        session, ctx, original, source_type=source_type, source_id=source_id, remarks=remarks
    )


async def position(
    session: AsyncSession, *, warehouse_id: UUID, material_id: UUID
) -> tuple[Decimal, Decimal]:
    """(quantity on hand, average cost) right now, without locking.

    For showing and validating what a person is about to do. Never for deciding
    what a movement is worth: `post` re-reads the balance under its lock.
    """
    row = (
        await session.execute(
            select(InventoryBalance.quantity_on_hand, InventoryBalance.average_cost).where(
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.material_id == material_id,
            )
        )
    ).first()
    return (row[0], row[1]) if row is not None else (Decimal(0), Decimal(0))


async def add_in_transit(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    warehouse_id: UUID,
    material_id: UUID,
    quantity: Decimal,
) -> None:
    """Move `quantity` (positive or negative) in or out of a balance's in-transit
    figure: stock dispatched to this warehouse and not yet counted in.

    It is a marker on the balance, not a ledger movement — the goods are on a
    truck, in neither store — so it never changes what is on hand or its value.
    """
    warehouse = await warehouse_lookup.get(
        session, company_id=ctx.company_id, warehouse_id=warehouse_id
    )
    if warehouse is None:
        raise _fail("warehouse_id", "Unknown warehouse")
    balance = await _locked_balance(session, ctx, warehouse, material_id)
    balance.quantity_in_transit = max(balance.quantity_in_transit + quantity, Decimal(0))
    await session.flush()
