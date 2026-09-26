"""GRN endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.access import AccessContext
from app.core.pagination import Page
from app.core.scoping import assert_in_scope
from app.modules.deliveries.services import receipts
from app.modules.grn.domain.enums import GrnStatus
from app.modules.grn.models import Grn
from app.modules.grn.schemas import (
    CancelBody,
    GrnFromDelivery,
    GrnItemRead,
    GrnListItem,
    GrnRead,
    InspectionIn,
)
from app.modules.grn.services import grn_service as service
from app.modules.masterdata.services import material_lookup, warehouse_lookup
from app.modules.org.services import document_lookup
from app.modules.procurement.services import po_lookup
from app.modules.vendors.services import vendor_lookup

router = APIRouter(prefix="/grns", tags=["grn"])
delivery_router = APIRouter(prefix="/deliveries", tags=["grn"])

IfMatch = Annotated[int | None, Header(alias="If-Match", description="The version you loaded")]
PERM_VIEW_PRICES = "inventory.view_valuation"


def _may(ctx: AccessContext, permission: str, grn: Grn) -> bool:
    try:
        assert_in_scope(
            ctx,
            permission,
            company_id=grn.company_id,
            project_id=grn.project_id,
            site_id=grn.site_id,
        )
    except Exception:  # noqa: BLE001 - any refusal simply means "no"
        return False
    return True


async def _detail(session: AsyncSession, ctx: AccessContext, grn: Grn) -> GrnRead:
    await session.refresh(grn)
    await session.refresh(grn, attribute_names=["items"])
    company = ctx.company_id
    materials = await material_lookup.materials(
        session, company_id=company, material_ids={i.material_id for i in grn.items}
    )
    units = await material_lookup.unit_codes(
        session,
        company_id=company,
        unit_ids={i.unit_id for i in grn.items} | {m.base_unit_id for m in materials.values()},
    )
    vendor = (
        await vendor_lookup.vendors(session, company_id=company, vendor_ids={grn.vendor_id})
    ).get(grn.vendor_id)
    codes = await document_lookup.codes(
        session, company_id=company, project_ids=set(), site_ids={grn.site_id}
    )
    wh = (
        await warehouse_lookup.names(session, company_id=company, warehouse_ids={grn.warehouse_id})
    ).get(grn.warehouse_id)
    order = (
        await po_lookup.order(session, company_id=company, purchase_order_id=grn.purchase_order_id)
        if grn.purchase_order_id
        else None
    )
    delivery_number = (
        await receipts.delivery_number(session, grn.delivery_id) if grn.delivery_id else None
    )
    hidden = not ctx.has(PERM_VIEW_PRICES)

    items = []
    for i in grn.items:
        read = GrnItemRead.model_validate(i)
        m = materials.get(i.material_id)
        read.material_sku = m.sku if m else None
        read.material_name = m.name if m else None
        read.unit_code = units.get(i.unit_id)
        read.base_unit_code = units.get(m.base_unit_id) if m else None
        if hidden:
            read.rate = read.amount = read.unit_cost = None
        items.append(read)

    view = GrnRead.model_validate(grn)
    view.items = items
    view.prices_hidden = hidden
    if hidden:
        view.gross_amount = view.net_amount = None
    if vendor:
        view.vendor_name = vendor.name
    view.site_code = codes.get(grn.site_id, (None, None))[0]
    if wh:
        view.warehouse_code, view.warehouse_name = wh
    view.purchase_order_number = order.po_number if order else None
    view.delivery_number = delivery_number
    draft = grn.status == GrnStatus.DRAFT.value
    view.has_unpriced_lines = any(i.accepted_quantity > 0 and i.amount is None for i in grn.items)
    view.can_inspect = draft and _may(ctx, service.PERM_CREATE, grn)
    view.can_reprice = draft and view.has_unpriced_lines and _may(ctx, service.PERM_CREATE, grn)
    view.can_post = draft and _may(ctx, service.PERM_APPROVE, grn)
    view.can_cancel = grn.status != GrnStatus.CANCELLED.value and _may(
        ctx, service.PERM_CANCEL, grn
    )
    return view


@router.get("", response_model=Page[GrnListItem], dependencies=[require(service.PERM_VIEW)])
async def list_grns(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    site_id: UUID | None = None,
    vendor_id: UUID | None = None,
    delivery_id: UUID | None = None,
    purchase_order_id: UUID | None = None,
) -> Page[GrnListItem]:
    rows, total = await service.list_grns(
        session,
        ctx,
        page=page,
        search=q,
        filters={
            "status": status,
            "site_id": site_id,
            "vendor_id": vendor_id,
            "delivery_id": delivery_id,
            "purchase_order_id": purchase_order_id,
        },
    )
    company = ctx.company_id
    vendors = await vendor_lookup.vendors(
        session, company_id=company, vendor_ids={r.vendor_id for r in rows}
    )
    codes = await document_lookup.codes(
        session, company_id=company, project_ids=set(), site_ids={r.site_id for r in rows}
    )
    whs = await warehouse_lookup.names(
        session, company_id=company, warehouse_ids={r.warehouse_id for r in rows}
    )
    hidden = not ctx.has(PERM_VIEW_PRICES)
    items = []
    for r in rows:
        item = GrnListItem.model_validate(r)
        item.site_code = codes.get(r.site_id, (None, None))[0]
        item.warehouse_code = whs.get(r.warehouse_id, (None, None))[0]
        item.vendor_name = vendors[r.vendor_id].name if r.vendor_id in vendors else None
        item.net_amount = None if hidden else r.net_amount
        items.append(item)
    return Page.of(items, params=page, total=total)


@router.get("/{grn_id}", response_model=GrnRead, dependencies=[require(service.PERM_VIEW)])
async def get_grn(grn_id: UUID, ctx: Access, session: SessionDep) -> GrnRead:
    return await _detail(session, ctx, await service.get(session, ctx, grn_id))


@delivery_router.post(
    "/{delivery_id}/convert-to-grn",
    response_model=GrnRead,
    status_code=201,
    dependencies=[require(service.PERM_CREATE)],
    summary="Raise a draft GRN from an approved delivery",
)
async def convert_to_grn(
    delivery_id: UUID, payload: GrnFromDelivery, ctx: Access, uow: UowDep
) -> GrnRead:
    grn = await service.create_from_delivery(uow.session, ctx, delivery_id, payload.warehouse_id)
    return await _detail(uow.session, ctx, grn)


@router.patch(
    "/{grn_id}/inspection",
    response_model=GrnRead,
    dependencies=[require(service.PERM_CREATE)],
    summary="Record what inspection found: how much of each line is taken, and why the rest is not",
)
async def inspect_grn(
    grn_id: UUID, payload: InspectionIn, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> GrnRead:
    grn = await service.inspect(
        uow.session,
        ctx,
        grn_id,
        [
            service.LineInspection(
                grn_item_id=line.grn_item_id,
                accepted_quantity=line.accepted_quantity,
                rejection_reason=line.rejection_reason,
                batch_no=line.batch_no,
                expiry_date=line.expiry_date,
            )
            for line in payload.lines
        ],
        warehouse_id=payload.warehouse_id,
        remarks=payload.remarks,
        expected_version=if_match,
    )
    return await _detail(uow.session, ctx, grn)


@router.post(
    "/{grn_id}/reprice",
    response_model=GrnRead,
    dependencies=[require(service.PERM_CREATE)],
    summary="Price lines that arrived unpriced, now a rate exists",
)
async def reprice_grn(grn_id: UUID, ctx: Access, uow: UowDep) -> GrnRead:
    return await _detail(uow.session, ctx, await service.reprice(uow.session, ctx, grn_id))


@router.post(
    "/{grn_id}/approve",
    response_model=GrnRead,
    dependencies=[require(service.PERM_APPROVE)],
    summary="Post the GRN: accepted quantities go into stock at their priced cost",
)
async def approve_grn(grn_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None) -> GrnRead:
    grn = await service.post(uow.session, ctx, grn_id, expected_version=if_match)
    return await _detail(uow.session, ctx, grn)


@router.post(
    "/{grn_id}/cancel",
    response_model=GrnRead,
    dependencies=[require(service.PERM_CANCEL)],
    summary="Cancel a GRN. If it was posted, the stock is reversed by contra entries.",
)
async def cancel_grn(grn_id: UUID, payload: CancelBody, ctx: Access, uow: UowDep) -> GrnRead:
    grn = await service.cancel(uow.session, ctx, grn_id, payload.reason)
    return await _detail(uow.session, ctx, grn)
