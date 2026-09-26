"""Delivery endpoints."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.modules.deliveries.api import views
from app.modules.deliveries.schemas import DeliveryCreate, DeliveryListItem, DeliveryRead
from app.modules.deliveries.services import ingest, queries
from app.modules.org.services import company_service

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


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
    result = await ingest.ingest(uow.session, ctx, ingest.input_from(payload))
    if result.duplicate:
        response.status_code = 200
    return await views.detail_view(uow.session, ctx, result.delivery)


@router.get(
    "/{delivery_id}", response_model=DeliveryRead, dependencies=[require(queries.PERM_VIEW)]
)
async def get_delivery(delivery_id: UUID, ctx: Access, session: SessionDep) -> DeliveryRead:
    return await views.detail_view(session, ctx, await queries.get(session, ctx, delivery_id))
