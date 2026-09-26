"""What the goods-received-note module needs to know about, and do to, a
delivery — kept in the deliveries module so nothing outside it touches its
tables (module boundary: tests/unit/test_architecture.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.errors import ConversionNotConfiguredError, NotFoundError
from app.core.scoping import assert_in_scope
from app.modules.deliveries.domain.enums import DeliveryStatus
from app.modules.deliveries.models import Delivery, DeliveryItem
from app.modules.masterdata.services.conversion import UnitConverter
from app.modules.procurement.services import po_lookup
from app.modules.rates.services import resolver as rate_resolver

_AMOUNT = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class ReceivableItem:
    id: UUID
    material_id: UUID
    quantity: Decimal
    unit_id: UUID
    po_item_id: UUID | None
    rate: Decimal | None
    vendor_rate_id: UUID | None
    amount: Decimal | None


@dataclass(frozen=True, slots=True)
class ReceivableDelivery:
    id: UUID
    delivery_number: str
    status: str
    site_id: UUID
    project_id: UUID | None
    vendor_id: UUID
    purchase_order_id: UUID | None
    grn_id: UUID | None
    captured_at: datetime
    items: tuple[ReceivableItem, ...]


async def get_receivable(
    session: AsyncSession, ctx: AccessContext, delivery_id: UUID
) -> ReceivableDelivery:
    row = (
        await session.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id, Delivery.company_id == ctx.company_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Delivery", delivery_id)
    assert_in_scope(
        ctx,
        "deliveries.view",
        company_id=row.company_id,
        project_id=row.project_id,
        site_id=row.site_id,
        entity="Delivery",
    )
    await session.refresh(row, attribute_names=["items"])
    return ReceivableDelivery(
        id=row.id,
        delivery_number=row.delivery_number,
        status=row.status,
        site_id=row.site_id,
        project_id=row.project_id,
        vendor_id=row.vendor_id,
        purchase_order_id=row.purchase_order_id,
        grn_id=row.grn_id,
        captured_at=row.captured_at,
        items=tuple(
            ReceivableItem(
                id=i.id,
                material_id=i.material_id,
                quantity=i.quantity,
                unit_id=i.unit_id,
                po_item_id=i.po_item_id,
                rate=i.rate,
                vendor_rate_id=i.vendor_rate_id,
                amount=i.amount,
            )
            for i in row.items
        ),
    )


async def ordered_quantities(
    session: AsyncSession, po_item_ids: list[UUID | None]
) -> dict[UUID, Decimal]:
    ids = [i for i in po_item_ids if i is not None]
    if not ids:
        return {}
    return await po_lookup.item_quantities(session, ids)


async def order_item_unit(session: AsyncSession, po_item_id: UUID) -> UUID | None:
    return await po_lookup.item_unit(session, po_item_id)


async def item_amount(session: AsyncSession, delivery_item_id: UUID | None) -> Decimal | None:
    if delivery_item_id is None:
        return None
    return await session.scalar(
        select(DeliveryItem.amount).where(DeliveryItem.id == delivery_item_id)
    )


async def link_grn(session: AsyncSession, delivery_id: UUID, grn_id: UUID) -> None:
    delivery = await session.get(Delivery, delivery_id)
    assert delivery is not None
    delivery.grn_id = grn_id
    delivery.version += 1
    await session.flush()


async def unlink_grn(session: AsyncSession, delivery_id: UUID) -> None:
    """A cancelled GRN puts its delivery back to approved, ready to be received again."""
    delivery = await session.get(Delivery, delivery_id)
    assert delivery is not None
    delivery.grn_id = None
    if delivery.status in (
        DeliveryStatus.RECEIVED.value,
        DeliveryStatus.PARTIALLY_RECEIVED.value,
    ):
        delivery.status = DeliveryStatus.APPROVED.value
    delivery.version += 1
    await session.flush()


async def mark_received(session: AsyncSession, delivery_id: UUID, *, partial: bool) -> None:
    delivery = await session.get(Delivery, delivery_id)
    assert delivery is not None
    delivery.status = (
        DeliveryStatus.PARTIALLY_RECEIVED if partial else DeliveryStatus.RECEIVED
    ).value
    delivery.version += 1
    await session.flush()


@dataclass(frozen=True, slots=True)
class PricedItem:
    rate: Decimal
    vendor_rate_id: UUID
    amount: Decimal


async def price_item(
    session: AsyncSession,
    ctx: AccessContext,
    converter: UnitConverter,
    *,
    delivery_item_id: UUID | None,
    vendor_id: UUID,
    project_id: UUID | None,
    site_id: UUID,
    captured_on: date,
) -> PricedItem | None:
    """Resolve a rate for a delivery line that was captured without one, and
    write the price back onto the delivery line (it was NULL: "priced later")."""
    if delivery_item_id is None:
        return None
    item = await session.get(DeliveryItem, delivery_item_id)
    if item is None:
        return None
    rate = await rate_resolver.resolve(
        session,
        company_id=ctx.company_id,
        vendor_id=vendor_id,
        material_id=item.material_id,
        at=captured_on,
        project_id=project_id,
        site_id=site_id,
    )
    if rate is None:
        return None
    try:
        converted = await converter.convert(
            item.quantity,
            item.unit_id,
            rate.unit_id,
            material_id=item.material_id,
            vendor_id=vendor_id,
            at=captured_on,
        )
    except ConversionNotConfiguredError:
        return None
    amount = (converted.converted_quantity * rate.rate).quantize(_AMOUNT, rounding=ROUND_HALF_UP)
    item.converted_quantity = converted.converted_quantity
    item.converted_unit_id = converted.converted_unit_id
    item.conversion_factor = converted.factor
    item.conversion_id = converted.conversion_id
    item.rate = rate.rate
    item.vendor_rate_id = rate.id
    item.rate_source = rate.source
    item.amount = amount
    await session.flush()
    return PricedItem(rate=rate.rate, vendor_rate_id=rate.id, amount=amount)


async def delivery_number(session: AsyncSession, delivery_id: UUID) -> str | None:
    found: str | None = await session.scalar(
        select(Delivery.delivery_number).where(Delivery.id == delivery_id)
    )
    return found
