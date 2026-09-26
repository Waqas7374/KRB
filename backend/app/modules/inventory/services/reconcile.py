"""Prove the cached balances still agree with the ledger (docs/02 §7).

`inventory_balances` is a projection of the append-only ledger, updated in the
same transaction as every row. It should never drift; this is the alarm for the
day it does (a manual fix, a bug, a half-restored backup).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import InventoryBalance, InventoryTransaction

_TOLERANCE = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class Drift:
    company_id: UUID
    warehouse_id: UUID
    material_id: UUID
    balance_quantity: Decimal
    ledger_quantity: Decimal
    balance_value: Decimal
    ledger_value: Decimal

    @property
    def detail(self) -> str:
        return (
            f"Stock of one material at one warehouse shows {self.balance_quantity:f} "
            f"(value {self.balance_value:f}) but the ledger adds up to "
            f"{self.ledger_quantity:f} (value {self.ledger_value:f})."
        )


async def find_drift(session: AsyncSession) -> list[Drift]:
    ledger = (
        select(
            InventoryTransaction.warehouse_id.label("warehouse_id"),
            InventoryTransaction.material_id.label("material_id"),
            func.coalesce(
                func.sum(InventoryTransaction.quantity_in - InventoryTransaction.quantity_out), 0
            ).label("quantity"),
            func.coalesce(
                func.sum(InventoryTransaction.value_in - InventoryTransaction.value_out), 0
            ).label("value"),
        )
        .group_by(InventoryTransaction.warehouse_id, InventoryTransaction.material_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            InventoryBalance.company_id,
            InventoryBalance.warehouse_id,
            InventoryBalance.material_id,
            InventoryBalance.quantity_on_hand,
            func.coalesce(ledger.c.quantity, 0),
            InventoryBalance.total_value,
            func.coalesce(ledger.c.value, 0),
        ).outerjoin(
            ledger,
            (ledger.c.warehouse_id == InventoryBalance.warehouse_id)
            & (ledger.c.material_id == InventoryBalance.material_id),
        )
    )
    drift = []
    for company, warehouse, material, bq, lq, bv, lv in rows.tuples():
        if (
            abs(Decimal(bq) - Decimal(lq)) > _TOLERANCE
            or abs(Decimal(bv) - Decimal(lv)) > _TOLERANCE
        ):
            drift.append(
                Drift(
                    company, warehouse, material, Decimal(bq), Decimal(lq), Decimal(bv), Decimal(lv)
                )
            )

    # A ledger with no balance row at all is drift too.
    orphans = await session.execute(
        select(
            InventoryTransaction.company_id,
            InventoryTransaction.warehouse_id,
            InventoryTransaction.material_id,
            func.sum(InventoryTransaction.quantity_in - InventoryTransaction.quantity_out),
            func.sum(InventoryTransaction.value_in - InventoryTransaction.value_out),
        )
        .outerjoin(
            InventoryBalance,
            (InventoryBalance.warehouse_id == InventoryTransaction.warehouse_id)
            & (InventoryBalance.material_id == InventoryTransaction.material_id),
        )
        .where(InventoryBalance.id.is_(None))
        .group_by(
            InventoryTransaction.company_id,
            InventoryTransaction.warehouse_id,
            InventoryTransaction.material_id,
        )
    )
    for company, warehouse, material, lq, lv in orphans.tuples():
        drift.append(
            Drift(company, warehouse, material, Decimal(0), Decimal(lq), Decimal(0), Decimal(lv))
        )
    return drift
