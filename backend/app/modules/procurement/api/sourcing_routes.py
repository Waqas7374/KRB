"""RFQ and quotation endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.access import AccessContext
from app.core.pagination import Page
from app.modules.identity.services import user_lookup
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.procurement.api.common import IfMatch, may
from app.modules.procurement.domain import pricing
from app.modules.procurement.domain.enums import QuotationStatus, RfqStatus
from app.modules.procurement.models import (
    PurchaseRequest,
    PurchaseRequestItem,
    Rfq,
    VendorQuotation,
)
from app.modules.procurement.services import purchase_orders, quotations, rfqs
from app.modules.procurement.sourcing_schemas import (
    ComparisonCellRead,
    ComparisonColumnRead,
    ComparisonRead,
    ComparisonRowRead,
    QuotationItemRead,
    QuotationListItem,
    QuotationRead,
    QuotationWrite,
    ReasonBody,
    RfqCreate,
    RfqInviteVendors,
    RfqItemRead,
    RfqListItem,
    RfqRead,
    RfqUpdate,
    RfqVendorRead,
    SelectQuotation,
)
from app.modules.vendors.services import vendor_lookup

rfq_router = APIRouter(prefix="/rfqs", tags=["procurement"])
quotation_router = APIRouter(prefix="/quotations", tags=["procurement"])

_OPEN_QUOTATION = {QuotationStatus.RECEIVED.value, QuotationStatus.SHORTLISTED.value}


# -----------------------------------------------------------------------------
# Views
# -----------------------------------------------------------------------------


def _lines(payload: RfqCreate | RfqUpdate) -> list[rfqs.RfqLineInput] | None:
    if payload.items is None:
        return None
    return [
        rfqs.RfqLineInput(
            material_id=i.material_id,
            quantity=i.quantity,
            unit_id=i.unit_id,
            description=i.description,
            pr_item_id=i.pr_item_id,
        )
        for i in payload.items
    ]


async def _rfq_view(session: AsyncSession, ctx: AccessContext, rfq: Rfq) -> RfqRead:
    await session.refresh(rfq)
    await session.refresh(rfq, attribute_names=["items", "vendors"])
    company_id = rfq.company_id
    materials = await material_lookup.materials(
        session, company_id=company_id, material_ids={i.material_id for i in rfq.items}
    )
    units = await material_lookup.unit_codes(
        session, company_id=company_id, unit_ids={i.unit_id for i in rfq.items}
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=company_id, vendor_ids={v.vendor_id for v in rfq.vendors}
    )
    codes = await document_lookup.codes(
        session,
        company_id=company_id,
        project_ids={rfq.project_id},
        site_ids={rfq.site_id} if rfq.site_id else set(),
    )
    answers = {
        q.vendor_id: q
        for q in (
            await session.execute(select(VendorQuotation).where(VendorQuotation.rfq_id == rfq.id))
        )
        .scalars()
        .unique()
    }
    estimates: dict[UUID, object] = {}
    pr_number = None
    if rfq.purchase_request_id:
        pr_number = await session.scalar(
            select(PurchaseRequest.pr_number).where(PurchaseRequest.id == rfq.purchase_request_id)
        )
        pr_item_ids = [i.pr_item_id for i in rfq.items if i.pr_item_id]
        if pr_item_ids:
            estimates = {
                row[0]: row[1]
                for row in (
                    await session.execute(
                        select(PurchaseRequestItem.id, PurchaseRequestItem.estimated_rate).where(
                            PurchaseRequestItem.id.in_(pr_item_ids)
                        )
                    )
                ).tuples()
            }

    items = [
        RfqItemRead.model_validate(
            {
                "id": i.id,
                "line_no": i.line_no,
                "material_id": i.material_id,
                "material_sku": materials[i.material_id].sku
                if i.material_id in materials
                else None,
                "material_name": materials[i.material_id].name
                if i.material_id in materials
                else None,
                "description": i.description,
                "quantity": i.quantity,
                "unit_id": i.unit_id,
                "unit_code": units.get(i.unit_id),
                "pr_item_id": i.pr_item_id,
                "estimated_rate": estimates.get(i.pr_item_id) if i.pr_item_id else None,
            }
        )
        for i in rfq.items
    ]
    invitations = []
    for v in rfq.vendors:
        answer = answers.get(v.vendor_id)
        info = vendors.get(v.vendor_id)
        invitations.append(
            RfqVendorRead(
                id=v.id,
                vendor_id=v.vendor_id,
                vendor_code=info.code if info else None,
                vendor_name=info.name if info else None,
                status=v.status,
                invited_at=v.invited_at,
                responded_at=v.responded_at,
                quotation_id=answer.id if answer else None,
                quotation_number=answer.quotation_number if answer else None,
                quotation_status=answer.status if answer else None,
                quotation_total=answer.total_amount if answer else None,
            )
        )
    view = RfqRead.model_validate(
        {
            **{
                c: getattr(rfq, c)
                for c in RfqRead.model_fields
                if hasattr(rfq, c) and c not in {"items", "vendors"}
            },
            "items": items,
            "vendors": invitations,
        }
    )
    view.pr_number = pr_number
    view.project_code, view.project_name = codes.get(rfq.project_id, (None, None))
    if rfq.site_id:
        view.site_code = codes.get(rfq.site_id, (None, None))[0]
    status = rfq.status
    view.can_edit = status == RfqStatus.DRAFT.value and may(ctx, rfqs.PERM_CREATE, rfq)
    view.can_issue = status == RfqStatus.DRAFT.value and may(ctx, rfqs.PERM_ISSUE, rfq)
    view.can_close = status == RfqStatus.ISSUED.value and may(ctx, rfqs.PERM_ISSUE, rfq)
    view.can_cancel = status in {
        RfqStatus.DRAFT.value,
        RfqStatus.ISSUED.value,
    } and may(ctx, rfqs.PERM_CREATE, rfq)
    view.can_record_quotation = status == RfqStatus.ISSUED.value and may(
        ctx, quotations.PERM_RECORD, rfq
    )
    return view


async def _quotation_view(
    session: AsyncSession, ctx: AccessContext, quotation: VendorQuotation
) -> QuotationRead:
    await session.refresh(quotation)
    await session.refresh(quotation, attribute_names=["items"])
    company_id = quotation.company_id
    materials = await material_lookup.materials(
        session, company_id=company_id, material_ids={i.material_id for i in quotation.items}
    )
    units = await material_lookup.unit_codes(
        session, company_id=company_id, unit_ids={i.unit_id for i in quotation.items}
    )
    vendor = (
        await vendor_lookup.vendors(
            session, company_id=company_id, vendor_ids={quotation.vendor_id}
        )
    ).get(quotation.vendor_id)
    rfq = await session.get(Rfq, quotation.rfq_id)
    people = await user_lookup.people(
        session,
        company_id=company_id,
        user_ids={quotation.selected_by_id} if quotation.selected_by_id else set(),
    )
    order = await quotations.live_order_for(session, quotation.id)

    view = QuotationRead.model_validate(
        {
            **{
                c: getattr(quotation, c)
                for c in QuotationRead.model_fields
                if hasattr(quotation, c) and c != "items"
            },
            "items": [
                QuotationItemRead.model_validate(
                    {
                        **{
                            c: getattr(i, c)
                            for c in QuotationItemRead.model_fields
                            if hasattr(i, c)
                        },
                        "material_sku": materials[i.material_id].sku
                        if i.material_id in materials
                        else None,
                        "material_name": materials[i.material_id].name
                        if i.material_id in materials
                        else None,
                        "unit_code": units.get(i.unit_id),
                        "net_rate": pricing.net_unit_rate(i.rate, i.discount_pct),
                    }
                )
                for i in quotation.items
            ],
        }
    )
    if vendor:
        view.vendor_code, view.vendor_name = vendor.code, vendor.name
    if rfq:
        view.rfq_number, view.rfq_status = rfq.rfq_number, rfq.status
    if quotation.selected_by_id in people:
        view.selected_by_name = people[quotation.selected_by_id].full_name
    if order:
        view.purchase_order_id, view.purchase_order_number = order.id, order.po_number
    rfq_open = rfq is not None and rfq.status == RfqStatus.ISSUED.value
    is_open = quotation.status in _OPEN_QUOTATION
    view.can_edit = is_open and rfq_open and may(ctx, quotations.PERM_RECORD, quotation)
    view.can_select = is_open and rfq_open and may(ctx, quotations.PERM_SELECT, quotation)
    selected = quotation.status == QuotationStatus.SELECTED.value
    view.can_withdraw = selected and order is None and may(ctx, quotations.PERM_SELECT, quotation)
    view.can_create_order = (
        selected and order is None and may(ctx, purchase_orders.PERM_CREATE, quotation)
    )
    return view


def _quotation_input(payload: QuotationWrite) -> quotations.QuotationInput:
    return quotations.QuotationInput(
        vendor_id=payload.vendor_id,
        vendor_reference=payload.vendor_reference,
        quote_date=payload.quote_date,
        valid_until=payload.valid_until,
        delivery_days=payload.delivery_days,
        payment_terms=payload.payment_terms,
        notes=payload.notes,
        items=[
            quotations.QuotationLineInput(
                rfq_item_id=i.rfq_item_id,
                rate=i.rate,
                discount_pct=i.discount_pct,
                tax_pct=i.tax_pct,
                quantity=i.quantity,
                delivery_days=i.delivery_days,
                remarks=i.remarks,
            )
            for i in payload.items
        ],
    )


# -----------------------------------------------------------------------------
# RFQs
# -----------------------------------------------------------------------------


@rfq_router.get("", response_model=Page[RfqListItem], dependencies=[require(rfqs.PERM_VIEW)])
async def list_rfqs(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    project_id: UUID | None = None,
    purchase_request_id: UUID | None = None,
) -> Page[RfqListItem]:
    rows, total = await rfqs.list_rfqs(
        session,
        ctx,
        page=page,
        search=q,
        filters={
            "status": status,
            "project_id": project_id,
            "purchase_request_id": purchase_request_id,
        },
    )
    codes = await document_lookup.codes(
        session,
        company_id=ctx.company_id,
        project_ids={r.project_id for r in rows},
        site_ids={r.site_id for r in rows if r.site_id},
    )
    ids = [r.id for r in rows]
    counts: dict[UUID, int] = {}
    if ids:
        counts = {
            row[0]: row[1]
            for row in (
                await session.execute(
                    select(VendorQuotation.rfq_id, func.count())
                    .where(VendorQuotation.rfq_id.in_(ids))
                    .group_by(VendorQuotation.rfq_id)
                )
            ).tuples()
        }
    pr_ids = {r.purchase_request_id for r in rows if r.purchase_request_id}
    numbers: dict[UUID, str] = {}
    if pr_ids:
        numbers = {
            row[0]: row[1]
            for row in (
                await session.execute(
                    select(PurchaseRequest.id, PurchaseRequest.pr_number).where(
                        PurchaseRequest.id.in_(pr_ids)
                    )
                )
            ).tuples()
        }
    items = []
    for r in rows:
        item = RfqListItem.model_validate(r)
        item.project_code = codes.get(r.project_id, (None, None))[0]
        item.site_code = codes.get(r.site_id, (None, None))[0] if r.site_id else None
        item.pr_number = numbers.get(r.purchase_request_id) if r.purchase_request_id else None
        item.item_count = len(r.items)
        item.vendor_count = len(r.vendors)
        item.quotation_count = counts.get(r.id, 0)
        items.append(item)
    return Page.of(items, params=page, total=total)


@rfq_router.post(
    "",
    response_model=RfqRead,
    status_code=201,
    dependencies=[require(rfqs.PERM_CREATE)],
    summary="Draft an RFQ, usually from an approved purchase request",
)
async def create_rfq(payload: RfqCreate, ctx: Access, uow: UowDep) -> RfqRead:
    rfq = await rfqs.create(
        uow.session,
        ctx,
        rfqs.RfqInput(
            # With a purchase request the RFQ takes that request's place.
            project_id=payload.project_id,
            site_id=payload.site_id,
            purchase_request_id=payload.purchase_request_id,
            title=payload.title,
            due_date=payload.due_date,
            terms=payload.terms,
            items=_lines(payload),
        ),
        payload.vendor_ids,
    )
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.get("/{rfq_id}", response_model=RfqRead, dependencies=[require(rfqs.PERM_VIEW)])
async def get_rfq(rfq_id: UUID, ctx: Access, session: SessionDep) -> RfqRead:
    return await _rfq_view(session, ctx, await rfqs.get(session, ctx, rfq_id))


@rfq_router.put(
    "/{rfq_id}",
    response_model=RfqRead,
    dependencies=[require(rfqs.PERM_CREATE)],
    summary="Replace a draft RFQ's content",
)
async def update_rfq(
    rfq_id: UUID, payload: RfqUpdate, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> RfqRead:
    rfq = await rfqs.update(
        uow.session,
        ctx,
        rfq_id,
        rfqs.RfqEdit(
            title=payload.title,
            due_date=payload.due_date,
            terms=payload.terms,
            items=_lines(payload) or [],
        ),
        expected_version=if_match,
    )
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.post(
    "/{rfq_id}/vendors",
    response_model=RfqRead,
    dependencies=[require(rfqs.PERM_CREATE)],
    summary="Invite more vendors (active vendors only)",
)
async def invite_vendors(
    rfq_id: UUID, payload: RfqInviteVendors, ctx: Access, uow: UowDep
) -> RfqRead:
    rfq = await rfqs.invite_vendors(uow.session, ctx, rfq_id, payload.vendor_ids)
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.delete(
    "/{rfq_id}/vendors/{rfq_vendor_id}",
    response_model=RfqRead,
    dependencies=[require(rfqs.PERM_CREATE)],
    summary="Withdraw an invitation (draft RFQs only)",
)
async def remove_vendor(rfq_id: UUID, rfq_vendor_id: UUID, ctx: Access, uow: UowDep) -> RfqRead:
    rfq = await rfqs.remove_vendor(uow.session, ctx, rfq_id, rfq_vendor_id)
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.post(
    "/{rfq_id}/vendors/{rfq_vendor_id}/decline",
    response_model=RfqRead,
    dependencies=[require(rfqs.PERM_CREATE)],
    summary="Record that an invited vendor declined to quote",
)
async def decline_vendor(rfq_id: UUID, rfq_vendor_id: UUID, ctx: Access, uow: UowDep) -> RfqRead:
    rfq = await rfqs.decline_vendor(uow.session, ctx, rfq_id, rfq_vendor_id)
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.post(
    "/{rfq_id}/issue",
    response_model=RfqRead,
    dependencies=[require(rfqs.PERM_ISSUE)],
    summary="Issue the RFQ to its invited vendors",
)
async def issue_rfq(rfq_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None) -> RfqRead:
    rfq = await rfqs.issue(uow.session, ctx, rfq_id, expected_version=if_match)
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.post("/{rfq_id}/close", response_model=RfqRead, dependencies=[require(rfqs.PERM_ISSUE)])
async def close_rfq(rfq_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep) -> RfqRead:
    rfq = await rfqs.close(uow.session, ctx, rfq_id, payload.reason)
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.post(
    "/{rfq_id}/cancel", response_model=RfqRead, dependencies=[require(rfqs.PERM_CREATE)]
)
async def cancel_rfq(rfq_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep) -> RfqRead:
    rfq = await rfqs.cancel(uow.session, ctx, rfq_id, payload.reason)
    return await _rfq_view(uow.session, ctx, rfq)


@rfq_router.get(
    "/{rfq_id}/comparison",
    response_model=ComparisonRead,
    dependencies=[require(quotations.PERM_VIEW)],
    summary="Vendors side by side, line by line",
)
async def comparison(rfq_id: UUID, ctx: Access, session: SessionDep) -> ComparisonRead:
    result = await quotations.compare(session, ctx, rfq_id)
    rfq = result.rfq
    materials = await material_lookup.materials(
        session, company_id=rfq.company_id, material_ids={i.material_id for i in rfq.items}
    )
    units = await material_lookup.unit_codes(
        session, company_id=rfq.company_id, unit_ids={i.unit_id for i in rfq.items}
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=rfq.company_id, vendor_ids={c.vendor_id for c in result.columns}
    )
    rows = [
        ComparisonRowRead(
            rfq_item_id=row.rfq_item.id,
            line_no=row.rfq_item.line_no,
            material_id=row.rfq_item.material_id,
            material_sku=materials[row.rfq_item.material_id].sku
            if row.rfq_item.material_id in materials
            else None,
            material_name=materials[row.rfq_item.material_id].name
            if row.rfq_item.material_id in materials
            else None,
            quantity=row.rfq_item.quantity,
            unit_code=units.get(row.rfq_item.unit_id),
            estimated_rate=row.estimated_rate,
            cells={
                vendor_id: ComparisonCellRead.model_validate(cell, from_attributes=True)
                for vendor_id, cell in row.cells.items()
            },
        )
        for row in result.rows
    ]
    columns = []
    for c in result.columns:
        q = c.quotation
        info = vendors.get(c.vendor_id)
        columns.append(
            ComparisonColumnRead(
                vendor_id=c.vendor_id,
                vendor_name=info.name if info else None,
                rfq_vendor_id=c.rfq_vendor_id,
                invitation_status=c.invitation_status,
                quotation_id=q.id if q else None,
                quotation_number=q.quotation_number if q else None,
                quotation_status=q.status if q else None,
                delivery_days=q.delivery_days if q else None,
                payment_terms=q.payment_terms if q else None,
                valid_until=q.valid_until if q else None,
                total_amount=q.total_amount if q else None,
                lines_quoted=c.lines_quoted,
                covers_all=c.covers_all,
                is_lowest_total=c.is_lowest_total,
                selection_reason=q.selection_reason if q else None,
            )
        )
    return ComparisonRead(
        rfq_id=rfq.id,
        rfq_number=rfq.rfq_number,
        rfq_status=rfq.status,
        currency_code=rfq.currency_code,
        columns=columns,
        rows=rows,
        can_select=rfq.status == RfqStatus.ISSUED.value and may(ctx, quotations.PERM_SELECT, rfq),
    )


@rfq_router.post(
    "/{rfq_id}/quotations",
    response_model=QuotationRead,
    status_code=201,
    dependencies=[require(quotations.PERM_RECORD)],
    summary="Record a quotation received from an invited vendor",
)
async def record_quotation(
    rfq_id: UUID, payload: QuotationWrite, ctx: Access, uow: UowDep
) -> QuotationRead:
    quotation = await quotations.record(uow.session, ctx, rfq_id, _quotation_input(payload))
    return await _quotation_view(uow.session, ctx, quotation)


# -----------------------------------------------------------------------------
# Quotations
# -----------------------------------------------------------------------------


@quotation_router.get(
    "", response_model=Page[QuotationListItem], dependencies=[require(quotations.PERM_VIEW)]
)
async def list_quotations(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    rfq_id: UUID | None = None,
    vendor_id: UUID | None = None,
) -> Page[QuotationListItem]:
    rows, total = await quotations.list_quotations(
        session,
        ctx,
        page=page,
        search=q,
        filters={"status": status, "rfq_id": rfq_id, "vendor_id": vendor_id},
    )
    vendors = await vendor_lookup.vendors(
        session, company_id=ctx.company_id, vendor_ids={r.vendor_id for r in rows}
    )
    rfq_numbers: dict[UUID, str] = {}
    if rows:
        rfq_numbers = {
            row[0]: row[1]
            for row in (
                await session.execute(
                    select(Rfq.id, Rfq.rfq_number).where(Rfq.id.in_({r.rfq_id for r in rows}))
                )
            ).tuples()
        }
    items = []
    for r in rows:
        item = QuotationListItem.model_validate(r)
        item.rfq_number = rfq_numbers.get(r.rfq_id)
        item.vendor_name = vendors[r.vendor_id].name if r.vendor_id in vendors else None
        items.append(item)
    return Page.of(items, params=page, total=total)


@quotation_router.get(
    "/{quotation_id}", response_model=QuotationRead, dependencies=[require(quotations.PERM_VIEW)]
)
async def get_quotation(quotation_id: UUID, ctx: Access, session: SessionDep) -> QuotationRead:
    return await _quotation_view(session, ctx, await quotations.get(session, ctx, quotation_id))


@quotation_router.put(
    "/{quotation_id}",
    response_model=QuotationRead,
    dependencies=[require(quotations.PERM_RECORD)],
    summary="Correct a received quotation (before it is selected)",
)
async def update_quotation(
    quotation_id: UUID,
    payload: QuotationWrite,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> QuotationRead:
    quotation = await quotations.update(
        uow.session, ctx, quotation_id, _quotation_input(payload), expected_version=if_match
    )
    return await _quotation_view(uow.session, ctx, quotation)


@quotation_router.post(
    "/{quotation_id}/shortlist",
    response_model=QuotationRead,
    dependencies=[require(quotations.PERM_RECORD)],
)
async def shortlist_quotation(quotation_id: UUID, ctx: Access, uow: UowDep) -> QuotationRead:
    quotation = await quotations.shortlist(uow.session, ctx, quotation_id)
    return await _quotation_view(uow.session, ctx, quotation)


@quotation_router.post(
    "/{quotation_id}/reject",
    response_model=QuotationRead,
    dependencies=[require(quotations.PERM_SELECT)],
)
async def reject_quotation(
    quotation_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep
) -> QuotationRead:
    quotation = await quotations.reject(uow.session, ctx, quotation_id, payload.reason)
    return await _quotation_view(uow.session, ctx, quotation)


@quotation_router.post(
    "/{quotation_id}/select",
    response_model=QuotationRead,
    dependencies=[require(quotations.PERM_SELECT)],
    summary="Choose the winning quotation. A reason is mandatory.",
)
async def select_quotation(
    quotation_id: UUID,
    payload: SelectQuotation,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> QuotationRead:
    quotation = await quotations.select_quotation(
        uow.session, ctx, quotation_id, payload.reason, expected_version=if_match
    )
    return await _quotation_view(uow.session, ctx, quotation)


@quotation_router.post(
    "/{quotation_id}/withdraw-selection",
    response_model=QuotationRead,
    dependencies=[require(quotations.PERM_SELECT)],
)
async def withdraw_selection(quotation_id: UUID, ctx: Access, uow: UowDep) -> QuotationRead:
    quotation = await quotations.withdraw_selection(uow.session, ctx, quotation_id)
    return await _quotation_view(uow.session, ctx, quotation)
