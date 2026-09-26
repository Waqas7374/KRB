"""Turn delivery rows into the API's read models, with the names people read."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.scoping import assert_in_scope
from app.modules.deliveries.domain.enums import DeliveryStatus, FlagSeverity, FlagStatus
from app.modules.deliveries.models import Delivery
from app.modules.deliveries.schemas import (
    DeliveryItemRead,
    DeliveryListItem,
    DeliveryRead,
    FlagRead,
    ReviewRead,
)
from app.modules.deliveries.services import review
from app.modules.identity.services import user_lookup
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.procurement.services import po_lookup
from app.modules.vendors.services import vendor_lookup

PERM_VIEW_PRICES = "rates.view"


def worst_open_severity(delivery: Delivery) -> str | None:
    open_flags = [f for f in delivery.flags if f.status == FlagStatus.OPEN.value]
    if not open_flags:
        return None
    return max(open_flags, key=lambda f: FlagSeverity(f.severity).rank).severity


async def list_views(
    session: AsyncSession, ctx: AccessContext, rows: list[Delivery]
) -> list[DeliveryListItem]:
    company = ctx.company_id
    vendors = await vendor_lookup.vendors(
        session, company_id=company, vendor_ids={r.vendor_id for r in rows}
    )
    codes = await document_lookup.codes(
        session,
        company_id=company,
        project_ids={r.project_id for r in rows if r.project_id},
        site_ids={r.site_id for r in rows},
    )
    materials = await material_lookup.materials(
        session,
        company_id=company,
        material_ids={i.material_id for r in rows for i in r.items},
    )
    show_prices = ctx.has(PERM_VIEW_PRICES)
    out: list[DeliveryListItem] = []
    for r in rows:
        item = DeliveryListItem.model_validate(r)
        item.site_code = codes.get(r.site_id, (None, None))[0]
        item.project_code = codes.get(r.project_id, (None, None))[0] if r.project_id else None
        vendor = vendors.get(r.vendor_id)
        item.vendor_name = vendor.name if vendor else None
        item.worst_severity = worst_open_severity(r)
        names = [materials[i.material_id].name for i in r.items if i.material_id in materials]
        item.material_summary = ", ".join(names) if names else None
        amounts = [i.amount for i in r.items if i.amount is not None]
        item.amount = sum(amounts) if show_prices and amounts else None  # type: ignore[assignment]
        out.append(item)
    return out


async def detail_view(
    session: AsyncSession, ctx: AccessContext, delivery: Delivery
) -> DeliveryRead:
    company = ctx.company_id
    await session.refresh(delivery)
    await session.refresh(delivery, attribute_names=["items", "flags"])
    materials = await material_lookup.materials(
        session, company_id=company, material_ids={i.material_id for i in delivery.items}
    )
    units = await material_lookup.unit_codes(
        session,
        company_id=company,
        unit_ids={i.unit_id for i in delivery.items}
        | {i.converted_unit_id for i in delivery.items if i.converted_unit_id},
    )
    vendor = (
        await vendor_lookup.vendors(session, company_id=company, vendor_ids={delivery.vendor_id})
    ).get(delivery.vendor_id)
    codes = await document_lookup.codes(
        session,
        company_id=company,
        project_ids={delivery.project_id} if delivery.project_id else set(),
        site_ids={delivery.site_id},
    )
    people = await user_lookup.people(
        session,
        company_id=company,
        user_ids={delivery.submitted_by_id} if delivery.submitted_by_id else set(),
    )
    trucks = await material_lookup.truck_type_names(
        session,
        company_id=company,
        truck_type_ids={delivery.truck_type_id} if delivery.truck_type_id else set(),
    )
    order = (
        await po_lookup.order(
            session, company_id=company, purchase_order_id=delivery.purchase_order_id
        )
        if delivery.purchase_order_id
        else None
    )
    hidden = not ctx.has(PERM_VIEW_PRICES)

    items = []
    for i in delivery.items:
        m = materials.get(i.material_id)
        read = DeliveryItemRead.model_validate(i)
        read.material_sku = m.sku if m else None
        read.material_name = m.name if m else None
        read.unit_code = units.get(i.unit_id)
        read.converted_unit_code = units.get(i.converted_unit_id) if i.converted_unit_id else None
        if hidden:
            read.rate = None
            read.amount = None
            read.rate_source = None
        items.append(read)

    view = DeliveryRead.model_validate(delivery, from_attributes=True)
    view.items = items
    view.flags = [FlagRead.model_validate(f) for f in delivery.flags]
    view.prices_hidden = hidden
    amounts = [i.amount for i in delivery.items if i.amount is not None]
    view.total_amount = None if hidden or not amounts else sum(amounts)  # type: ignore[assignment]
    view.site_code, view.site_name = codes.get(delivery.site_id, (None, None))
    if delivery.project_id:
        view.project_code = codes.get(delivery.project_id, (None, None))[0]
    if vendor:
        view.vendor_code, view.vendor_name = vendor.code, vendor.name
    if delivery.submitted_by_id in people:
        view.submitted_by_name = people[delivery.submitted_by_id].full_name
    if delivery.truck_type_id:
        view.truck_type_name = trucks.get(delivery.truck_type_id)
    if order:
        view.purchase_order_number = order.po_number
    view.reviews = [
        ReviewRead.model_validate(r) for r in await review.reviews_for(session, ctx, delivery.id)
    ]
    _capabilities(view, ctx, delivery)
    return view


def _may(ctx: AccessContext, permission: str, delivery: Delivery) -> bool:
    try:
        assert_in_scope(
            ctx,
            permission,
            company_id=delivery.company_id,
            project_id=delivery.project_id,
            site_id=delivery.site_id,
        )
    except Exception:  # noqa: BLE001 - any refusal simply means "no"
        return False
    return True


def _capabilities(view: DeliveryRead, ctx: AccessContext, delivery: Delivery) -> None:
    status = delivery.status
    deciding = status in (DeliveryStatus.SUBMITTED.value, DeliveryStatus.UNDER_REVIEW.value)
    view.can_approve = deciding and _may(ctx, review.PERM_APPROVE, delivery)
    view.can_reject = (deciding or status == DeliveryStatus.CORRECTION_REQUESTED.value) and _may(
        ctx, review.PERM_REJECT, delivery
    )
    view.can_request_correction = deciding and _may(ctx, review.PERM_CORRECTION, delivery)
    view.can_reopen = (
        status in (DeliveryStatus.REJECTED.value, DeliveryStatus.APPROVED.value)
        and delivery.grn_id is None
        and _may(ctx, review.PERM_REOPEN, delivery)
    )
    view.can_attach_order = (
        delivery.purchase_order_id is None and deciding and _may(ctx, review.PERM_REVIEW, delivery)
    )
    view.can_correct = DeliveryStatus(status).is_editable and (
        delivery.submitted_by_id == ctx.user_id or ctx.has("deliveries.update_draft")
    )
    view.can_waive = status == DeliveryStatus.UNDER_REVIEW.value and _may(
        ctx, review.PERM_WAIVE, delivery
    )
