"""Review endpoints: the queue, decisions, corrections, flag waivers."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.pagination import Page
from app.modules.deliveries.api import views
from app.modules.deliveries.api.routes import _input
from app.modules.deliveries.schemas import (
    AttachOrder,
    DeliveryCreate,
    DeliveryListItem,
    DeliveryRead,
    ReasonBody,
    ReviewRead,
    WaiveFlag,
)
from app.modules.deliveries.services import ingest, queries, review

router = APIRouter(prefix="/deliveries", tags=["deliveries"])
flag_router = APIRouter(prefix="/delivery-flags", tags=["deliveries"])


@router.get(
    "/review-queue",
    response_model=Page[DeliveryListItem],
    dependencies=[require(review.PERM_REVIEW)],
    summary="Deliveries waiting for a decision: most severe first, oldest first within a severity",
)
async def review_queue(ctx: Access, session: SessionDep, page: PageDep) -> Page[DeliveryListItem]:
    rows, total = await review.review_queue(session, ctx, page=page)
    return Page.of(await views.list_views(session, ctx, rows), params=page, total=total)


@router.get(
    "/{delivery_id}/reviews",
    response_model=list[ReviewRead],
    dependencies=[require(queries.PERM_VIEW)],
    summary="Every decision made on a delivery, newest first. Append-only.",
)
async def delivery_reviews(delivery_id: UUID, ctx: Access, session: SessionDep) -> list[ReviewRead]:
    await queries.get(session, ctx, delivery_id)  # 404 when out of scope
    return [
        ReviewRead.model_validate(r) for r in await review.reviews_for(session, ctx, delivery_id)
    ]


@router.put(
    "/{delivery_id}",
    response_model=DeliveryRead,
    dependencies=[require(ingest.PERM_CREATE)],
    summary="Correct a delivery a reviewer sent back. It is checked afresh.",
)
async def correct_delivery(
    delivery_id: UUID, payload: DeliveryCreate, ctx: Access, uow: UowDep
) -> DeliveryRead:
    result = await ingest.correct(uow.session, ctx, delivery_id, _input(payload))
    await review.record_correction(uow.session, ctx, result)
    return await views.detail_view(uow.session, ctx, result.delivery)


@router.post(
    "/{delivery_id}/approve",
    response_model=DeliveryRead,
    dependencies=[require(review.PERM_APPROVE)],
    summary="Approve; resolves any open flags as accepted (critical ones need a reason)",
)
async def approve_delivery(
    delivery_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep
) -> DeliveryRead:
    row = await review.accept(uow.session, ctx, delivery_id, payload.comments)
    return await views.detail_view(uow.session, ctx, row)


@router.post(
    "/{delivery_id}/reject",
    response_model=DeliveryRead,
    dependencies=[require(review.PERM_REJECT)],
)
async def reject_delivery(
    delivery_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep
) -> DeliveryRead:
    row = await review.reject(uow.session, ctx, delivery_id, payload.comments or "")
    return await views.detail_view(uow.session, ctx, row)


@router.post(
    "/{delivery_id}/request-correction",
    response_model=DeliveryRead,
    dependencies=[require(review.PERM_CORRECTION)],
    summary="Send it back to the person who captured it, with a note",
)
async def request_correction(
    delivery_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep
) -> DeliveryRead:
    row = await review.request_correction(uow.session, ctx, delivery_id, payload.comments or "")
    return await views.detail_view(uow.session, ctx, row)


@router.post(
    "/{delivery_id}/reopen",
    response_model=DeliveryRead,
    dependencies=[require(review.PERM_REOPEN)],
)
async def reopen_delivery(
    delivery_id: UUID, payload: ReasonBody, ctx: Access, uow: UowDep
) -> DeliveryRead:
    row = await review.reopen(uow.session, ctx, delivery_id, payload.comments or "")
    return await views.detail_view(uow.session, ctx, row)


@router.post(
    "/{delivery_id}/attach-purchase-order",
    response_model=DeliveryRead,
    dependencies=[require(review.PERM_REVIEW)],
    summary="Attach an order to a delivery that arrived without one (re-checks the order balance)",
)
async def attach_purchase_order(
    delivery_id: UUID, payload: AttachOrder, ctx: Access, uow: UowDep
) -> DeliveryRead:
    row = await review.attach_purchase_order(
        uow.session, ctx, delivery_id, payload.purchase_order_id, payload.comments
    )
    return await views.detail_view(uow.session, ctx, row)


@flag_router.post(
    "/{flag_id}/waive",
    response_model=DeliveryRead,
    dependencies=[require(review.PERM_WAIVE)],
    summary="Set one flag aside, with a reason",
)
async def waive_flag(flag_id: UUID, payload: WaiveFlag, ctx: Access, uow: UowDep) -> DeliveryRead:
    row = await review.waive_flag(uow.session, ctx, flag_id, payload.note)
    return await views.detail_view(uow.session, ctx, row)
