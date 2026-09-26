"""Purchase order endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.access import AccessContext
from app.core.errors import PermissionDeniedError
from app.core.pagination import Page
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import company_service, document_lookup
from app.modules.procurement.api.common import IfMatch, may
from app.modules.procurement.api.po_document import render_purchase_order
from app.modules.procurement.domain.enums import PurchaseOrderStatus
from app.modules.procurement.models import (
    PurchaseOrder,
    PurchaseRequest,
    PurchaseRequestItem,
    Rfq,
    VendorQuotation,
)
from app.modules.procurement.services import purchase_orders as service
from app.modules.procurement.sourcing_schemas import (
    PurchaseOrderItemRead,
    PurchaseOrderListItem,
    PurchaseOrderRead,
    PurchaseOrderWrite,
    ReasonBody,
)
from app.modules.vendors.services import vendor_lookup
from app.platform.pdf import html_to_pdf

router = APIRouter(prefix="/purchase-orders", tags=["procurement"])

_PRICE_FIELDS = ("rate", "discount_pct", "tax_pct", "tax_amount", "line_total")
_CANCELLABLE = {
    PurchaseOrderStatus.DRAFT.value,
    PurchaseOrderStatus.REJECTED.value,
    PurchaseOrderStatus.CHANGES_REQUESTED.value,
    PurchaseOrderStatus.APPROVED.value,
    PurchaseOrderStatus.SENT.value,
    PurchaseOrderStatus.ACKNOWLEDGED.value,
}
_CLOSABLE = {
    PurchaseOrderStatus.SENT.value,
    PurchaseOrderStatus.ACKNOWLEDGED.value,
    PurchaseOrderStatus.PARTIALLY_RECEIVED.value,
    PurchaseOrderStatus.RECEIVED.value,
}
_AMENDABLE = {
    PurchaseOrderStatus.APPROVED.value,
    PurchaseOrderStatus.SENT.value,
    PurchaseOrderStatus.ACKNOWLEDGED.value,
}


def _input(payload: PurchaseOrderWrite) -> service.PoInput:
    return service.PoInput(
        vendor_id=payload.vendor_id,
        project_id=payload.project_id,
        site_id=payload.site_id,
        phase_id=payload.phase_id,
        cost_center_id=payload.cost_center_id,
        expected_delivery_date=payload.expected_delivery_date,
        delivery_address=payload.delivery_address,
        payment_terms=payload.payment_terms,
        terms_and_conditions=payload.terms_and_conditions,
        items=[
            service.PoLineInput(
                material_id=i.material_id,
                quantity=i.quantity,
                unit_id=i.unit_id,
                rate=i.rate,
                discount_pct=i.discount_pct,
                tax_pct=i.tax_pct,
                description=i.description,
                pr_item_id=i.pr_item_id,
            )
            for i in payload.items
        ],
    )


async def _detail(
    session: AsyncSession, ctx: AccessContext, po: PurchaseOrder
) -> PurchaseOrderRead:
    await session.refresh(po)
    await session.refresh(po, attribute_names=["items"])
    company_id = po.company_id
    materials = await material_lookup.materials(
        session, company_id=company_id, material_ids={i.material_id for i in po.items}
    )
    units = await material_lookup.unit_codes(
        session, company_id=company_id, unit_ids={i.unit_id for i in po.items}
    )
    vendor = (
        await vendor_lookup.vendors(session, company_id=company_id, vendor_ids={po.vendor_id})
    ).get(po.vendor_id)
    codes = await document_lookup.codes(
        session,
        company_id=company_id,
        project_ids={po.project_id},
        site_ids={po.site_id} if po.site_id else set(),
    )
    pr_item_ids = {i.pr_item_id for i in po.items if i.pr_item_id}
    pr_numbers: dict[UUID, str] = {}
    if pr_item_ids:
        pr_numbers = {
            row[0]: row[1]
            for row in (
                await session.execute(
                    select(PurchaseRequestItem.id, PurchaseRequest.pr_number)
                    .join(PurchaseRequest, PurchaseRequest.id == PurchaseRequestItem.request_id)
                    .where(PurchaseRequestItem.id.in_(pr_item_ids))
                )
            ).tuples()
        }
    hidden = not ctx.has(service.PERM_VIEW_PRICING)

    def item_view(i: object) -> PurchaseOrderItemRead:
        data = {
            c: getattr(i, c)
            for c in PurchaseOrderItemRead.model_fields
            if hasattr(i, c) and c not in _PRICE_FIELDS
        }
        if not hidden:
            data.update({c: getattr(i, c) for c in _PRICE_FIELDS})
        m = materials.get(i.material_id)  # type: ignore[attr-defined]
        return PurchaseOrderItemRead.model_validate(
            {
                **data,
                "material_sku": m.sku if m else None,
                "material_name": m.name if m else None,
                "unit_code": units.get(i.unit_id),  # type: ignore[attr-defined]
                "pr_number": pr_numbers.get(i.pr_item_id) if i.pr_item_id else None,  # type: ignore[attr-defined]
            }
        )

    money = ("subtotal", "discount_amount", "tax_amount", "total_amount")
    view = PurchaseOrderRead.model_validate(
        {
            **{
                c: getattr(po, c)
                for c in PurchaseOrderRead.model_fields
                if hasattr(po, c) and c not in {"items", *money}
            },
            **({} if hidden else {c: getattr(po, c) for c in money}),
            "prices_hidden": hidden,
            "items": [item_view(i) for i in po.items],
        }
    )
    if vendor:
        view.vendor_code, view.vendor_name = vendor.code, vendor.name
    view.project_code, view.project_name = codes.get(po.project_id, (None, None))
    if po.site_id:
        view.site_code, view.site_name = codes.get(po.site_id, (None, None))
    if po.quotation_id:
        quotation = await session.get(VendorQuotation, po.quotation_id)
        if quotation:
            view.quotation_number = quotation.quotation_number
            rfq = await session.get(Rfq, quotation.rfq_id)
            if rfq:
                view.rfq_id, view.rfq_number = rfq.id, rfq.rfq_number

    status = po.status
    received = any(i.received_quantity > 0 for i in po.items)
    editable = PurchaseOrderStatus(status).is_editable
    view.can_edit = editable and may(ctx, service.PERM_CREATE, po)
    view.can_submit = editable and may(ctx, service.PERM_CREATE, po)
    view.can_amend = status in _AMENDABLE and not received and may(ctx, service.PERM_AMEND, po)
    view.can_send = status == PurchaseOrderStatus.APPROVED.value and may(ctx, service.PERM_SEND, po)
    view.can_acknowledge = status == PurchaseOrderStatus.SENT.value and may(
        ctx, service.PERM_SEND, po
    )
    view.can_cancel = status in _CANCELLABLE and not received and may(ctx, service.PERM_CANCEL, po)
    view.can_close = status in _CLOSABLE and may(ctx, service.PERM_CANCEL, po)
    return view


@router.get(
    "", response_model=Page[PurchaseOrderListItem], dependencies=[require(service.PERM_VIEW)]
)
async def list_orders(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    project_id: UUID | None = None,
    site_id: UUID | None = None,
    vendor_id: UUID | None = None,
) -> Page[PurchaseOrderListItem]:
    rows, total = await service.list_orders(
        session,
        ctx,
        page=page,
        search=q,
        filters={
            "status": status,
            "project_id": project_id,
            "site_id": site_id,
            "vendor_id": vendor_id,
        },
    )
    codes = await document_lookup.codes(
        session,
        company_id=ctx.company_id,
        project_ids={r.project_id for r in rows},
        site_ids={r.site_id for r in rows if r.site_id},
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={r.vendor_id for r in rows}
    )
    hidden = not ctx.has(service.PERM_VIEW_PRICING)
    items = []
    for r in rows:
        item = PurchaseOrderListItem.model_validate(r)
        item.project_code = codes.get(r.project_id, (None, None))[0]
        item.site_code = codes.get(r.site_id, (None, None))[0] if r.site_id else None
        item.vendor_name = vendors[r.vendor_id].name if r.vendor_id in vendors else None
        if hidden:
            item.total_amount = None
        items.append(item)
    return Page.of(items, params=page, total=total)


@router.post(
    "",
    response_model=PurchaseOrderRead,
    status_code=201,
    dependencies=[require(service.PERM_CREATE)],
    summary="Raise a purchase order without an RFQ (starts as a draft)",
)
async def create_order(payload: PurchaseOrderWrite, ctx: Access, uow: UowDep) -> PurchaseOrderRead:
    po = await service.create(uow.session, ctx, _input(payload))
    return await _detail(uow.session, ctx, po)


@router.post(
    "/from-quotation/{quotation_id}",
    response_model=PurchaseOrderRead,
    status_code=201,
    dependencies=[require(service.PERM_CREATE)],
    summary="Raise a draft order from the selected quotation",
)
async def create_from_quotation(quotation_id: UUID, ctx: Access, uow: UowDep) -> PurchaseOrderRead:
    po = await service.create_from_quotation(uow.session, ctx, quotation_id)
    return await _detail(uow.session, ctx, po)


@router.get("/{po_id}", response_model=PurchaseOrderRead, dependencies=[require(service.PERM_VIEW)])
async def get_order(po_id: UUID, ctx: Access, session: SessionDep) -> PurchaseOrderRead:
    return await _detail(session, ctx, await service.get(session, ctx, po_id))


@router.put(
    "/{po_id}",
    response_model=PurchaseOrderRead,
    dependencies=[require(service.PERM_CREATE)],
    summary="Replace a draft, rejected or returned order's content",
)
async def update_order(
    po_id: UUID,
    payload: PurchaseOrderWrite,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> PurchaseOrderRead:
    po = await service.update(uow.session, ctx, po_id, _input(payload), expected_version=if_match)
    return await _detail(uow.session, ctx, po)


@router.post(
    "/{po_id}/submit",
    response_model=PurchaseOrderRead,
    dependencies=[require(service.PERM_CREATE)],
    summary="Submit for approval — routed by the active purchase-order workflow",
)
async def submit_order(
    po_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> PurchaseOrderRead:
    po = await service.submit(uow.session, ctx, po_id, expected_version=if_match)
    return await _detail(uow.session, ctx, po)


@router.post(
    "/{po_id}/amend",
    response_model=PurchaseOrderRead,
    dependencies=[require(service.PERM_AMEND)],
    summary="Reopen an approved order for change; it must be approved again",
)
async def amend_order(
    po_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> PurchaseOrderRead:
    po = await service.amend(uow.session, ctx, po_id, payload.reason, expected_version=if_match)
    return await _detail(uow.session, ctx, po)


@router.post(
    "/{po_id}/send", response_model=PurchaseOrderRead, dependencies=[require(service.PERM_SEND)]
)
async def send_order(
    po_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> PurchaseOrderRead:
    po = await service.send(uow.session, ctx, po_id, expected_version=if_match)
    return await _detail(uow.session, ctx, po)


@router.post(
    "/{po_id}/acknowledge",
    response_model=PurchaseOrderRead,
    dependencies=[require(service.PERM_SEND)],
    summary="Record that the vendor confirmed the order",
)
async def acknowledge_order(
    po_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> PurchaseOrderRead:
    po = await service.acknowledge(uow.session, ctx, po_id, expected_version=if_match)
    return await _detail(uow.session, ctx, po)


@router.post(
    "/{po_id}/cancel",
    response_model=PurchaseOrderRead,
    dependencies=[require(service.PERM_CANCEL)],
)
async def cancel_order(
    po_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep
) -> PurchaseOrderRead:
    po = await service.cancel(uow.session, ctx, po_id, payload.reason)
    return await _detail(uow.session, ctx, po)


@router.post(
    "/{po_id}/close",
    response_model=PurchaseOrderRead,
    dependencies=[require(service.PERM_CANCEL)],
    summary="End an order that was sent; unreceived quantity returns to the request",
)
async def close_order(
    po_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep
) -> PurchaseOrderRead:
    po = await service.close(uow.session, ctx, po_id, payload.reason)
    return await _detail(uow.session, ctx, po)


@router.get(
    "/{po_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
    dependencies=[require(service.PERM_VIEW)],
    summary="The printable order. Needs permission to see prices.",
)
async def order_pdf(po_id: UUID, ctx: Access, session: SessionDep) -> Response:
    # A printout without amounts is useless to a vendor, and one with them
    # must not reach someone who may not see them.
    if not ctx.has(service.PERM_VIEW_PRICING):
        raise PermissionDeniedError(service.PERM_VIEW_PRICING)
    po = await _detail(session, ctx, await service.get(session, ctx, po_id))
    company = await company_service.get_profile(session, ctx.company_id)
    document = await html_to_pdf(
        render_purchase_order(po, company_name=company.legal_name or company.name)
    )
    return Response(
        content=document,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{po.po_number}.pdf"'},
    )
