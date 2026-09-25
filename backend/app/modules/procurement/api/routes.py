"""Purchase request endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.access import AccessContext
from app.core.pagination import Page
from app.core.scoping import assert_in_scope
from app.modules.identity.services import user_lookup
from app.modules.masterdata.services import material_lookup
from app.modules.org.services import document_lookup
from app.modules.procurement.domain.enums import PurchaseRequestStatus
from app.modules.procurement.models import PurchaseRequest
from app.modules.procurement.schemas import (
    PurchaseRequestCancel,
    PurchaseRequestCreate,
    PurchaseRequestItemRead,
    PurchaseRequestListItem,
    PurchaseRequestRead,
    PurchaseRequestUpdate,
)
from app.modules.procurement.services import purchase_requests as service

router = APIRouter(prefix="/purchase-requests", tags=["procurement"])

IfMatch = Annotated[int | None, Header(alias="If-Match", description="The version you loaded")]


def _input(payload: PurchaseRequestCreate) -> service.RequestInput:
    return service.RequestInput(
        project_id=payload.project_id,
        site_id=payload.site_id,
        department_id=payload.department_id,
        cost_center_id=payload.cost_center_id,
        phase_id=payload.phase_id,
        required_date=payload.required_date,
        priority=payload.priority.value,
        justification=payload.justification,
        items=[
            service.LineInput(
                material_id=i.material_id,
                quantity=i.quantity,
                unit_id=i.unit_id,
                estimated_rate=i.estimated_rate,
                description=i.description,
                required_date=i.required_date,
            )
            for i in payload.items
        ],
    )


def _may(ctx: AccessContext, permission: str, pr: PurchaseRequest) -> bool:
    try:
        assert_in_scope(
            ctx,
            permission,
            company_id=pr.company_id,
            project_id=pr.project_id,
            site_id=pr.site_id,
            department_id=pr.department_id,
        )
    except Exception:  # noqa: BLE001 — any refusal simply means "no"
        return False
    return True


async def _detail(
    session: AsyncSession, ctx: AccessContext, pr: PurchaseRequest
) -> PurchaseRequestRead:
    # A write just flushed an UPDATE, which expires server-side columns such as
    # updated_at; reading them synchronously below would need a lazy load
    # (MissingGreenlet). Reload the row and its lines explicitly.
    await session.refresh(pr)
    await session.refresh(pr, attribute_names=["items"])
    materials = await material_lookup.materials(
        session, company_id=pr.company_id, material_ids={i.material_id for i in pr.items}
    )
    units = await material_lookup.unit_codes(
        session, company_id=pr.company_id, unit_ids={i.unit_id for i in pr.items}
    )
    codes = await document_lookup.codes(
        session,
        company_id=pr.company_id,
        project_ids={pr.project_id},
        site_ids={pr.site_id} if pr.site_id else set(),
        phase_ids={pr.phase_id} if pr.phase_id else set(),
    )
    people = await user_lookup.people(
        session,
        company_id=pr.company_id,
        user_ids={pr.requested_by_id} if pr.requested_by_id else set(),
    )
    editable = PurchaseRequestStatus(pr.status).is_editable
    view = PurchaseRequestRead.model_validate(
        {
            **{
                c: getattr(pr, c)
                for c in PurchaseRequestRead.model_fields
                if hasattr(pr, c) and c != "items"
            },
            "items": [
                PurchaseRequestItemRead.model_validate(
                    {
                        **{
                            c: getattr(i, c)
                            for c in PurchaseRequestItemRead.model_fields
                            if hasattr(i, c)
                        },
                        "material_sku": materials[i.material_id].sku
                        if i.material_id in materials
                        else None,
                        "material_name": materials[i.material_id].name
                        if i.material_id in materials
                        else None,
                        "unit_code": units.get(i.unit_id),
                    }
                )
                for i in pr.items
            ],
        }
    )
    view.project_code, view.project_name = codes.get(pr.project_id, (None, None))
    if pr.site_id:
        view.site_code, view.site_name = codes.get(pr.site_id, (None, None))
    if pr.phase_id:
        view.phase_code = codes.get(pr.phase_id, (None, None))[0]
    if pr.requested_by_id in people:
        view.requested_by_name = people[pr.requested_by_id].full_name
    view.can_edit = editable and _may(ctx, service.PERM_CREATE, pr)
    view.can_submit = editable and _may(ctx, service.PERM_SUBMIT, pr)
    view.can_cancel = pr.status in {
        PurchaseRequestStatus.DRAFT.value,
        PurchaseRequestStatus.REJECTED.value,
        PurchaseRequestStatus.CHANGES_REQUESTED.value,
        PurchaseRequestStatus.APPROVED.value,
    } and _may(ctx, service.PERM_CREATE, pr)
    return view


@router.get(
    "", response_model=Page[PurchaseRequestListItem], dependencies=[require(service.PERM_VIEW)]
)
async def list_requests(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    project_id: UUID | None = None,
    site_id: UUID | None = None,
    mine: bool = False,
) -> Page[PurchaseRequestListItem]:
    filters: dict[str, object] = {"status": status, "project_id": project_id, "site_id": site_id}
    if mine:
        filters["requested_by_id"] = ctx.user_id
    rows, total = await service.list_requests(session, ctx, page=page, search=q, filters=filters)
    codes = await document_lookup.codes(
        session,
        company_id=ctx.company_id,
        project_ids={r.project_id for r in rows},
        site_ids={r.site_id for r in rows if r.site_id},
    )
    people = await user_lookup.people(
        session,
        company_id=ctx.company_id,
        user_ids={r.requested_by_id for r in rows if r.requested_by_id},
    )
    items = []
    for r in rows:
        item = PurchaseRequestListItem.model_validate(r)
        item.project_code = codes.get(r.project_id, (None, None))[0]
        item.site_code = codes.get(r.site_id, (None, None))[0] if r.site_id else None
        item.item_count = len(r.items)
        item.requested_by_name = (
            people[r.requested_by_id].full_name if r.requested_by_id in people else None
        )
        items.append(item)
    return Page.of(items, params=page, total=total)


@router.post(
    "",
    response_model=PurchaseRequestRead,
    status_code=201,
    dependencies=[require(service.PERM_CREATE)],
    summary="Raise a purchase request (starts as a draft)",
)
async def create_request(
    payload: PurchaseRequestCreate, ctx: Access, uow: UowDep
) -> PurchaseRequestRead:
    pr = await service.create(uow.session, ctx, _input(payload))
    return await _detail(uow.session, ctx, pr)


@router.get(
    "/{request_id}", response_model=PurchaseRequestRead, dependencies=[require(service.PERM_VIEW)]
)
async def get_request(request_id: UUID, ctx: Access, session: SessionDep) -> PurchaseRequestRead:
    return await _detail(session, ctx, await service.get(session, ctx, request_id))


@router.put(
    "/{request_id}",
    response_model=PurchaseRequestRead,
    dependencies=[require(service.PERM_CREATE)],
    summary="Replace a draft, rejected or returned request's content",
)
async def update_request(
    request_id: UUID,
    payload: PurchaseRequestUpdate,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> PurchaseRequestRead:
    pr = await service.update(
        uow.session, ctx, request_id, _input(payload), expected_version=if_match
    )
    return await _detail(uow.session, ctx, pr)


@router.post(
    "/{request_id}/submit",
    response_model=PurchaseRequestRead,
    dependencies=[require(service.PERM_SUBMIT)],
    summary="Submit for approval — routed by the active purchase-request workflow",
)
async def submit_request(
    request_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> PurchaseRequestRead:
    pr = await service.submit(uow.session, ctx, request_id, expected_version=if_match)
    return await _detail(uow.session, ctx, pr)


@router.post(
    "/{request_id}/cancel",
    response_model=PurchaseRequestRead,
    dependencies=[require(service.PERM_CREATE)],
)
async def cancel_request(
    request_id: UUID, payload: PurchaseRequestCancel, ctx: Access, uow: UowDep
) -> PurchaseRequestRead:
    pr = await service.cancel(uow.session, ctx, request_id, payload.reason)
    return await _detail(uow.session, ctx, pr)
