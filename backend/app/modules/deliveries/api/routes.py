"""Delivery endpoints."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.core.types import utcnow
from app.modules.deliveries.api import views
from app.modules.deliveries.schemas import DeliveryCreate, DeliveryListItem, DeliveryRead
from app.modules.deliveries.services import ingest, queries
from app.modules.org.services import company_service

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


def _input(payload: DeliveryCreate) -> ingest.DeliveryInput:
    return ingest.DeliveryInput(
        id=payload.id,
        site_id=payload.site_id,
        vendor_id=payload.vendor_id,
        purchase_order_id=payload.purchase_order_id,
        po_item_id=payload.po_item_id,
        truck_number=payload.truck_number,
        truck_type_id=payload.truck_type_id,
        driver_name=payload.driver_name,
        driver_phone=payload.driver_phone,
        challan_number=payload.challan_number,
        challan_date=payload.challan_date,
        captured_at=payload.captured_at or utcnow(),
        latitude=payload.latitude,
        longitude=payload.longitude,
        gps_accuracy_m=payload.gps_accuracy_m,
        location_source=payload.location_source.value,
        remarks=payload.remarks,
        device_id=payload.device_id,
        app_version=payload.app_version,
        was_offline=payload.was_offline,
        device_time=payload.device_time,
        items=[
            ingest.LineInput(
                material_id=i.material_id,
                quantity=i.quantity,
                unit_id=i.unit_id,
                remarks=i.remarks,
            )
            for i in payload.items
        ],
    )


@router.get("", response_model=Page[DeliveryListItem], dependencies=[require(queries.PERM_VIEW)])
async def list_deliveries(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    site_id: UUID | None = None,
    project_id: UUID | None = None,
    vendor_id: UUID | None = None,
    purchase_order_id: UUID | None = None,
    has_open_flags: bool | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> Page[DeliveryListItem]:
    zone = ZoneInfo((await company_service.get_profile(session, ctx.company_id)).timezone)
    rows, total = await queries.list_deliveries(
        session,
        ctx,
        page=page,
        search=q,
        filters={
            "status": status,
            "site_id": site_id,
            "project_id": project_id,
            "vendor_id": vendor_id,
            "purchase_order_id": purchase_order_id,
        },
        captured_from=datetime.combine(from_date, time.min, tzinfo=zone) if from_date else None,
        captured_before=(
            datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=zone)
            if to_date
            else None
        ),
        only_open_flags=bool(has_open_flags),
    )
    return Page.of(await views.list_views(session, ctx, rows), params=page, total=total)


@router.post(
    "",
    response_model=DeliveryRead,
    status_code=201,
    dependencies=[require(ingest.PERM_CREATE)],
    summary="Record a delivery. It is always saved; anything odd becomes a flag.",
    responses={
        200: {"description": "This id was already recorded; the stored delivery is returned"}
    },
)
async def create_delivery(
    payload: DeliveryCreate, ctx: Access, uow: UowDep, response: Response
) -> DeliveryRead:
    result = await ingest.ingest(uow.session, ctx, _input(payload))
    if result.duplicate:
        response.status_code = 200
    return await views.detail_view(uow.session, ctx, result.delivery)


@router.get(
    "/{delivery_id}", response_model=DeliveryRead, dependencies=[require(queries.PERM_VIEW)]
)
async def get_delivery(delivery_id: UUID, ctx: Access, session: SessionDep) -> DeliveryRead:
    return await views.detail_view(session, ctx, await queries.get(session, ctx, delivery_id))
