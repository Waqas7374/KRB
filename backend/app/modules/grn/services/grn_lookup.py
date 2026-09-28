"""GRN facts other modules need (finance matches a vendor invoice against what
was actually received), exposed without the ORM models (module boundary:
tests/unit/test_architecture.py)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.grn.models import Grn, GrnItem


@dataclass(frozen=True, slots=True)
class GrnItemInfo:
    id: UUID
    grn_id: UUID
    grn_number: str
    po_item_id: UUID | None
    material_id: UUID
    vendor_id: UUID
    accepted_quantity: Decimal
    # What it was actually valued at when received — the figure a 3-way
    # match's rate variance is really asking about, not the order's rate.
    unit_cost: Decimal | None
    amount: Decimal | None


async def item(session: AsyncSession, *, company_id: UUID, grn_item_id: UUID) -> GrnItemInfo | None:
    row = (
        await session.execute(
            select(GrnItem, Grn.grn_number, Grn.vendor_id)
            .join(Grn, Grn.id == GrnItem.grn_id)
            .where(GrnItem.id == grn_item_id, Grn.company_id == company_id)
        )
    ).one_or_none()
    if row is None:
        return None
    grn_item, grn_number, vendor_id = row
    return GrnItemInfo(
        id=grn_item.id,
        grn_id=grn_item.grn_id,
        grn_number=grn_number,
        po_item_id=grn_item.po_item_id,
        material_id=grn_item.material_id,
        vendor_id=vendor_id,
        accepted_quantity=grn_item.accepted_quantity,
        unit_cost=grn_item.unit_cost,
        amount=grn_item.amount,
    )
