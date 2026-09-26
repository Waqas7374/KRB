"""Open purchase orders a phone can pick from (docs/06 §4, docs/12 Q3).

An order is on the device while it can be received against. When it stops being
receivable (cancelled, closed, fully received) it arrives as a tombstone, so the
picker never offers an order the server would refuse.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.scoping import scope_filter
from app.core.sync import FeedRow
from app.modules.procurement.domain.enums import PurchaseOrderStatus
from app.modules.procurement.models import PurchaseOrder
from app.modules.procurement.services.po_lookup import RECEIVABLE

CAPTURE_PERMISSION = "deliveries.create"


async def changes(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    since: int,
    limit: int,
    site_id: UUID | None,
) -> list[FeedRow]:
    stmt = (
        scope_filter(select(PurchaseOrder), PurchaseOrder, ctx, CAPTURE_PERMISSION)
        .where(PurchaseOrder.server_seq.is_not(None), PurchaseOrder.server_seq > since)
        .order_by(PurchaseOrder.server_seq)
        .limit(limit)
    )
    if site_id is not None:
        stmt = stmt.where((PurchaseOrder.site_id == site_id) | (PurchaseOrder.site_id.is_(None)))
    rows = (await session.execute(stmt)).scalars().unique().all()
    out = []
    for po in rows:
        receivable = PurchaseOrderStatus(po.status) in RECEIVABLE
        out.append(
            FeedRow(
                "open_pos",
                po.id,
                int(po.server_seq or 0),
                deleted=not receivable,
                data={
                    "po_number": po.po_number,
                    "vendor_id": str(po.vendor_id),
                    "project_id": str(po.project_id),
                    "site_id": str(po.site_id) if po.site_id else None,
                    "status": po.status,
                    "expected_delivery_date": po.expected_delivery_date.isoformat()
                    if po.expected_delivery_date
                    else None,
                    "items": [
                        {
                            "id": str(i.id),
                            "material_id": str(i.material_id),
                            "unit_id": str(i.unit_id),
                            "quantity": str(i.quantity),
                            "received_quantity": str(i.received_quantity),
                        }
                        for i in po.items
                    ],
                }
                if receivable
                else {},
            )
        )
    return out
