"""Purchase-order facts other modules need (deliveries receive against orders),
exposed without the ORM models (module boundary: tests/unit/test_architecture.py)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.procurement.domain.enums import PurchaseOrderStatus
from app.modules.procurement.models import PurchaseOrder

# An order can be received against once it stands, until it ends.
RECEIVABLE = frozenset(
    {
        PurchaseOrderStatus.APPROVED,
        PurchaseOrderStatus.SENT,
        PurchaseOrderStatus.ACKNOWLEDGED,
        PurchaseOrderStatus.PARTIALLY_RECEIVED,
    }
)


@dataclass(frozen=True, slots=True)
class OrderItemInfo:
    id: UUID
    line_no: int
    material_id: UUID
    unit_id: UUID
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderInfo:
    id: UUID
    po_number: str
    vendor_id: UUID
    project_id: UUID
    site_id: UUID | None
    phase_id: UUID | None
    cost_center_id: UUID | None
    status: str
    items: tuple[OrderItemInfo, ...]

    @property
    def receivable(self) -> bool:
        return PurchaseOrderStatus(self.status) in RECEIVABLE


async def order(
    session: AsyncSession, *, company_id: UUID, purchase_order_id: UUID
) -> OrderInfo | None:
    row = (
        await session.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.company_id == company_id, PurchaseOrder.id == purchase_order_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    await session.refresh(row, attribute_names=["items"])
    return OrderInfo(
        id=row.id,
        po_number=row.po_number,
        vendor_id=row.vendor_id,
        project_id=row.project_id,
        site_id=row.site_id,
        phase_id=row.phase_id,
        cost_center_id=row.cost_center_id,
        status=row.status,
        items=tuple(
            OrderItemInfo(
                id=i.id,
                line_no=i.line_no,
                material_id=i.material_id,
                unit_id=i.unit_id,
                quantity=i.quantity,
            )
            for i in row.items
        ),
    )


async def item_quantities(session: AsyncSession, po_item_ids: list[UUID]) -> dict[UUID, Decimal]:
    from app.modules.procurement.models import PurchaseOrderItem

    rows = await session.execute(
        select(PurchaseOrderItem.id, PurchaseOrderItem.quantity).where(
            PurchaseOrderItem.id.in_(po_item_ids)
        )
    )
    return dict(rows.tuples().all())


async def item_unit(session: AsyncSession, po_item_id: UUID) -> UUID | None:
    from app.modules.procurement.models import PurchaseOrderItem

    found: UUID | None = await session.scalar(
        select(PurchaseOrderItem.unit_id).where(PurchaseOrderItem.id == po_item_id)
    )
    return found
