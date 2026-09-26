"""Receiving against purchase orders.

Goods-received notes tell the order what arrived; the order line keeps the
running totals (received, accepted) that make 3-way matching possible without
recomputing history (docs/02), and the order moves to PARTIALLY_RECEIVED /
RECEIVED as those totals fill. Quantities are in the *order line's* unit; the
caller converts.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.modules.procurement.domain.enums import PurchaseOrderStatus
from app.modules.procurement.models import PurchaseOrder, PurchaseOrderItem

_OPEN = {
    PurchaseOrderStatus.APPROVED.value,
    PurchaseOrderStatus.SENT.value,
    PurchaseOrderStatus.ACKNOWLEDGED.value,
    PurchaseOrderStatus.PARTIALLY_RECEIVED.value,
    PurchaseOrderStatus.RECEIVED.value,
}


async def apply_receipt(
    session: AsyncSession, *, po_item_id: UUID, received: Decimal, accepted: Decimal
) -> None:
    """Add (or, negative, take back) received and accepted quantity on an order
    line, then bring the order's status into line with its lines."""
    item = (
        await session.execute(
            select(PurchaseOrderItem).where(PurchaseOrderItem.id == po_item_id).with_for_update()
        )
    ).scalar_one_or_none()
    if item is None:
        return
    new_received = item.received_quantity + received
    new_accepted = item.accepted_quantity + accepted
    if new_received < 0 or new_accepted < 0:
        raise BusinessRuleError(
            "purchase_order_receipt_negative",
            "That would take more back from the order than was ever received against it.",
        )
    item.received_quantity = new_received
    item.accepted_quantity = new_accepted
    await session.flush()

    order = (
        await session.execute(
            select(PurchaseOrder).where(PurchaseOrder.id == item.po_id).with_for_update()
        )
    ).scalar_one()
    await session.refresh(order, attribute_names=["items"])
    if order.status not in _OPEN:
        return  # a closed or cancelled order keeps its status; only the totals move
    if all(i.received_quantity >= i.quantity for i in order.items):
        target = PurchaseOrderStatus.RECEIVED
    elif any(i.received_quantity > 0 for i in order.items):
        target = PurchaseOrderStatus.PARTIALLY_RECEIVED
    elif order.acknowledged_at is not None:
        target = PurchaseOrderStatus.ACKNOWLEDGED
    elif order.sent_at is not None:
        target = PurchaseOrderStatus.SENT
    else:
        target = PurchaseOrderStatus.APPROVED
    if order.status != target.value:
        order.status = target.value
        order.version += 1
    await session.flush()
