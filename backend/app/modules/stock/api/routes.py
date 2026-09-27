"""Stock issue, transfer and adjustment endpoints (docs/07 §inventory)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, UowDep, require
from app.core.access import AccessContext
from app.core.pagination import Page
from app.modules.masterdata.services import material_lookup, warehouse_lookup
from app.modules.org.services import document_lookup, site_lookup
from app.modules.stock.domain.enums import AdjustmentStatus, IssueStatus, TransferStatus
from app.modules.stock.models import StockAdjustment, StockIssue, StockTransfer
from app.modules.stock.schemas import (
    AdjustmentCreate,
    AdjustmentLineRead,
    AdjustmentListItem,
    AdjustmentRead,
    CancelBody,
    IssueCreate,
    IssueLineRead,
    IssueListItem,
    IssueRead,
    LineRead,
    TransferCreate,
    TransferListItem,
    TransferRead,
    WarehouseOptionRead,
)
from app.modules.stock.services import adjustments, issues, transfers

issue_router = APIRouter(prefix="/inventory/issues", tags=["stock issues"])
transfer_router = APIRouter(prefix="/inventory/transfers", tags=["stock transfers"])
adjustment_router = APIRouter(prefix="/inventory/adjustments", tags=["stock adjustments"])
option_router = APIRouter(prefix="/inventory", tags=["stock"])

IfMatch = Annotated[int | None, Header(alias="If-Match", description="The version you loaded")]
PERM_VALUATION = "inventory.view_valuation"


def _can(
    ctx: AccessContext,
    permission: str,
    *,
    project_id: UUID | None,
    site_id: UUID | None,
) -> bool:
    return ctx.can(permission, company_id=ctx.company_id, project_id=project_id, site_id=site_id)


class _Names:
    """The names people read, looked up once per response."""

    def __init__(
        self,
        materials: dict[UUID, material_lookup.MaterialInfo],
        units: dict[UUID, str],
        warehouses: dict[UUID, tuple[str, str]],
        sites: Mapping[UUID, tuple[str | None, str | None]],
    ) -> None:
        self.materials = materials
        self.units = units
        self.warehouses = warehouses
        self.sites = sites

    def warehouse_code(self, warehouse_id: UUID) -> str | None:
        found = self.warehouses.get(warehouse_id)
        return found[0] if found else None

    def site_code(self, site_id: UUID) -> str | None:
        return self.sites.get(site_id, (None, None))[0]


async def _names(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    material_ids: set[UUID],
    unit_ids: set[UUID],
    warehouse_ids: set[UUID],
    site_ids: set[UUID],
) -> _Names:
    company = ctx.company_id
    materials = await material_lookup.materials(
        session, company_id=company, material_ids=material_ids
    )
    units = await material_lookup.unit_codes(
        session,
        company_id=company,
        unit_ids=unit_ids | {m.base_unit_id for m in materials.values()},
    )
    warehouses = await warehouse_lookup.names(
        session, company_id=company, warehouse_ids=warehouse_ids
    )
    sites = await document_lookup.codes(
        session, company_id=company, project_ids=set(), site_ids=site_ids
    )
    return _Names(materials, units, warehouses, sites)


def _line(read: LineRead, names: _Names, hidden: bool) -> LineRead:
    m = names.materials.get(read.material_id)
    read.material_sku = m.sku if m else None
    read.material_name = m.name if m else None
    read.unit_code = names.units.get(read.unit_id)
    read.base_unit_code = names.units.get(m.base_unit_id) if m else None
    if hidden:
        read.unit_cost = None
    return read


# -----------------------------------------------------------------------------
# Pickers
# -----------------------------------------------------------------------------

_ACTION_PERMISSION = {
    "issue": issues.PERM_ISSUE,
    "adjust": adjustments.PERM_ADJUST,
    "transfer_from": transfers.PERM_TRANSFER,
    # Where a GRN (including a counter purchase) may be raised; a literal, so `stock`
    # does not depend on the `grn` module for one permission code.
    "receive": "grn.create",
}


@option_router.get(
    "/warehouse-options",
    response_model=list[WarehouseOptionRead],
    dependencies=[require(issues.PERM_VIEW)],
    summary="The stores a person may act on, for the pickers on stock forms",
    description=(
        "`issue`, `adjust`, `receive` and `transfer_from` return only stores at sites the "
        "caller may act on. `transfer_to` returns every store: sending stock to another site "
        "is the point of a transfer. Needs no warehouse-management permission."
    ),
)
async def warehouse_options(
    ctx: Access,
    session: SessionDep,
    action: Literal["issue", "adjust", "transfer_from", "transfer_to", "receive"],
) -> list[WarehouseOptionRead]:
    company = ctx.company_id
    if action == "transfer_to":
        if not ctx.has(transfers.PERM_TRANSFER):
            return []
        site_ids: set[UUID] | None = None
        codes = await site_lookup.site_codes_in_scope(
            session, company_id=company, site_ids=None, project_ids=None, everything=True
        )
    else:
        permission = _ACTION_PERMISSION[action]
        if not ctx.has(permission):
            return []
        scope = ctx.scope_for(permission)
        codes = await site_lookup.site_codes_in_scope(
            session,
            company_id=company,
            site_ids=scope.site_ids,
            project_ids=scope.project_ids,
            everything=scope.is_company_wide,
        )
        site_ids = set(codes)
    rows = await warehouse_lookup.options(session, company_id=company, site_ids=site_ids)
    return [
        WarehouseOptionRead(
            id=w.id,
            code=w.code,
            name=w.name,
            site_id=w.site_id,
            site_code=codes.get(w.site_id),
            is_default_receiving=w.is_default_receiving,
        )
        for w in rows
    ]


# -----------------------------------------------------------------------------
# Issues
# -----------------------------------------------------------------------------


async def _issue_view(session: AsyncSession, ctx: AccessContext, issue: StockIssue) -> IssueRead:
    await session.refresh(issue)
    await session.refresh(issue, attribute_names=["items"])
    names = await _names(
        session,
        ctx,
        material_ids={i.material_id for i in issue.items},
        unit_ids={i.unit_id for i in issue.items},
        warehouse_ids={issue.warehouse_id},
        site_ids={issue.site_id},
    )
    hidden = not ctx.has(PERM_VALUATION)
    view = IssueRead.model_validate(issue)
    view.items = []
    for item in issue.items:
        line = IssueLineRead.model_validate(item)
        _line(line, names, hidden)
        if hidden:
            line.value = None
        view.items.append(line)
    view.warehouse_code = names.warehouse_code(issue.warehouse_id)
    found = names.warehouses.get(issue.warehouse_id)
    view.warehouse_name = found[1] if found else None
    view.site_code = names.site_code(issue.site_id)
    view.prices_hidden = hidden
    view.total_value = None if hidden else issues.total_value(issue)
    draft = issue.status == IssueStatus.DRAFT.value
    allowed = _can(ctx, issues.PERM_ISSUE, project_id=issue.project_id, site_id=issue.site_id)
    view.can_post = draft and allowed
    view.can_cancel = issue.status != IssueStatus.CANCELLED.value and allowed
    return view


@issue_router.get("", response_model=Page[IssueListItem], dependencies=[require(issues.PERM_VIEW)])
async def list_issues(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    site_id: UUID | None = None,
    warehouse_id: UUID | None = None,
) -> Page[IssueListItem]:
    rows, total = await issues.list_issues(
        session,
        ctx,
        page=page,
        search=q,
        filters={"status": status, "site_id": site_id, "warehouse_id": warehouse_id},
    )
    names = await _names(
        session,
        ctx,
        material_ids=set(),
        unit_ids=set(),
        warehouse_ids={r.warehouse_id for r in rows},
        site_ids={r.site_id for r in rows},
    )
    items = []
    for r in rows:
        item = IssueListItem.model_validate(r)
        item.warehouse_code = names.warehouse_code(r.warehouse_id)
        item.site_code = names.site_code(r.site_id)
        items.append(item)
    return Page.of(items, params=page, total=total)


@issue_router.get("/{issue_id}", response_model=IssueRead, dependencies=[require(issues.PERM_VIEW)])
async def get_issue(issue_id: UUID, ctx: Access, session: SessionDep) -> IssueRead:
    return await _issue_view(session, ctx, await issues.get(session, ctx, issue_id))


@issue_router.post(
    "",
    response_model=IssueRead,
    status_code=201,
    dependencies=[require(issues.PERM_ISSUE)],
    summary="Raise an issue as a draft; nothing leaves the store until it is posted",
)
async def create_issue(payload: IssueCreate, ctx: Access, uow: UowDep) -> IssueRead:
    return await _issue_view(uow.session, ctx, await issues.create(uow.session, ctx, payload))


@issue_router.post(
    "/{issue_id}/post",
    response_model=IssueRead,
    dependencies=[require(issues.PERM_ISSUE)],
    summary="Post the issue: stock leaves the store at its current average cost",
)
async def post_issue(
    issue_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> IssueRead:
    issue = await issues.post(uow.session, ctx, issue_id, expected_version=if_match)
    return await _issue_view(uow.session, ctx, issue)


@issue_router.post(
    "/{issue_id}/cancel",
    response_model=IssueRead,
    dependencies=[require(issues.PERM_ISSUE)],
    summary="Cancel an issue. If it was posted, the stock goes back by contra entries.",
)
async def cancel_issue(issue_id: UUID, payload: CancelBody, ctx: Access, uow: UowDep) -> IssueRead:
    issue = await issues.cancel(uow.session, ctx, issue_id, payload.reason)
    return await _issue_view(uow.session, ctx, issue)


# -----------------------------------------------------------------------------
# Transfers
# -----------------------------------------------------------------------------


async def _transfer_view(
    session: AsyncSession, ctx: AccessContext, transfer: StockTransfer
) -> TransferRead:
    await session.refresh(transfer)
    await session.refresh(transfer, attribute_names=["items"])
    names = await _names(
        session,
        ctx,
        material_ids={i.material_id for i in transfer.items},
        unit_ids={i.unit_id for i in transfer.items},
        warehouse_ids={transfer.from_warehouse_id, transfer.to_warehouse_id},
        site_ids={transfer.site_id, transfer.to_site_id},
    )
    hidden = not ctx.has(PERM_VALUATION)
    view = TransferRead.model_validate(transfer)
    view.items = [_line(LineRead.model_validate(i), names, hidden) for i in transfer.items]
    _transfer_names(view, names)
    view.prices_hidden = hidden
    at_source = _can(
        ctx, transfers.PERM_TRANSFER, project_id=transfer.project_id, site_id=transfer.site_id
    )
    at_destination = _can(
        ctx,
        transfers.PERM_TRANSFER,
        project_id=transfer.to_project_id,
        site_id=transfer.to_site_id,
    )
    view.can_dispatch = transfer.status == TransferStatus.DRAFT.value and at_source
    view.can_receive = transfer.status == TransferStatus.IN_TRANSIT.value and at_destination
    view.can_cancel = (
        transfer.status in (TransferStatus.DRAFT.value, TransferStatus.IN_TRANSIT.value)
        and at_source
    )
    return view


def _transfer_names(view: TransferListItem, names: _Names) -> None:
    view.from_warehouse_code = names.warehouse_code(view.from_warehouse_id)
    view.to_warehouse_code = names.warehouse_code(view.to_warehouse_id)
    view.site_code = names.site_code(view.site_id)
    view.to_site_code = names.site_code(view.to_site_id)


@transfer_router.get(
    "", response_model=Page[TransferListItem], dependencies=[require(transfers.PERM_VIEW)]
)
async def list_transfers(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    site_id: UUID | None = None,
    to_site_id: UUID | None = None,
) -> Page[TransferListItem]:
    rows, total = await transfers.list_transfers(
        session,
        ctx,
        page=page,
        search=q,
        filters={"status": status, "site_id": site_id, "to_site_id": to_site_id},
    )
    names = await _names(
        session,
        ctx,
        material_ids=set(),
        unit_ids=set(),
        warehouse_ids={r.from_warehouse_id for r in rows} | {r.to_warehouse_id for r in rows},
        site_ids={r.site_id for r in rows} | {r.to_site_id for r in rows},
    )
    items = []
    for r in rows:
        item = TransferListItem.model_validate(r)
        _transfer_names(item, names)
        items.append(item)
    return Page.of(items, params=page, total=total)


@transfer_router.get(
    "/{transfer_id}", response_model=TransferRead, dependencies=[require(transfers.PERM_VIEW)]
)
async def get_transfer(transfer_id: UUID, ctx: Access, session: SessionDep) -> TransferRead:
    return await _transfer_view(session, ctx, await transfers.get(session, ctx, transfer_id))


@transfer_router.post(
    "",
    response_model=TransferRead,
    status_code=201,
    dependencies=[require(transfers.PERM_TRANSFER)],
    summary="Raise a transfer as a draft; nothing moves until it is dispatched",
)
async def create_transfer(payload: TransferCreate, ctx: Access, uow: UowDep) -> TransferRead:
    return await _transfer_view(uow.session, ctx, await transfers.create(uow.session, ctx, payload))


@transfer_router.post(
    "/{transfer_id}/dispatch",
    response_model=TransferRead,
    dependencies=[require(transfers.PERM_TRANSFER)],
    summary="Dispatch: stock leaves the source store and shows as in transit at the destination",
)
async def dispatch_transfer(transfer_id: UUID, ctx: Access, uow: UowDep) -> TransferRead:
    return await _transfer_view(
        uow.session, ctx, await transfers.dispatch(uow.session, ctx, transfer_id)
    )


@transfer_router.post(
    "/{transfer_id}/receive",
    response_model=TransferRead,
    dependencies=[require(transfers.PERM_TRANSFER)],
    summary="Receive: the stock is counted into the destination at the cost it left at",
)
async def receive_transfer(transfer_id: UUID, ctx: Access, uow: UowDep) -> TransferRead:
    return await _transfer_view(
        uow.session, ctx, await transfers.receive(uow.session, ctx, transfer_id)
    )


@transfer_router.post(
    "/{transfer_id}/cancel",
    response_model=TransferRead,
    dependencies=[require(transfers.PERM_TRANSFER)],
    summary="Cancel a draft, or bring back a transfer still in transit",
)
async def cancel_transfer(
    transfer_id: UUID, payload: CancelBody, ctx: Access, uow: UowDep
) -> TransferRead:
    transfer = await transfers.cancel(uow.session, ctx, transfer_id, payload.reason)
    return await _transfer_view(uow.session, ctx, transfer)


# -----------------------------------------------------------------------------
# Adjustments
# -----------------------------------------------------------------------------


async def _adjustment_view(
    session: AsyncSession, ctx: AccessContext, adjustment: StockAdjustment
) -> AdjustmentRead:
    await session.refresh(adjustment)
    await session.refresh(adjustment, attribute_names=["items"])
    names = await _names(
        session,
        ctx,
        material_ids={i.material_id for i in adjustment.items},
        unit_ids=set(),
        warehouse_ids={adjustment.warehouse_id},
        site_ids={adjustment.site_id},
    )
    hidden = not ctx.has(PERM_VALUATION)
    view = AdjustmentRead.model_validate(adjustment)
    view.items = []
    for item in adjustment.items:
        line = AdjustmentLineRead.model_validate(item)
        m = names.materials.get(item.material_id)
        line.material_sku = m.sku if m else None
        line.material_name = m.name if m else None
        line.unit_code = names.units.get(m.base_unit_id) if m else None
        if hidden:
            line.unit_cost = line.value_delta = None
        view.items.append(line)
    view.warehouse_code = names.warehouse_code(adjustment.warehouse_id)
    found = names.warehouses.get(adjustment.warehouse_id)
    view.warehouse_name = found[1] if found else None
    view.site_code = names.site_code(adjustment.site_id)
    view.prices_hidden = hidden
    view.net_value = None if hidden else adjustments.totals(adjustment)[1]

    status = AdjustmentStatus(adjustment.status)
    allowed = _can(
        ctx, adjustments.PERM_ADJUST, project_id=adjustment.project_id, site_id=adjustment.site_id
    )
    view.can_edit = status.is_editable and allowed
    view.can_submit = status.is_editable and allowed
    view.can_cancel = status.is_editable and allowed
    view.can_withdraw = (
        status is AdjustmentStatus.PENDING_APPROVAL
        and allowed
        and adjustment.updated_by_id == ctx.user_id
    )
    return view


@adjustment_router.get(
    "", response_model=Page[AdjustmentListItem], dependencies=[require(adjustments.PERM_VIEW)]
)
async def list_adjustments(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    q: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    site_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    reason_code: str | None = None,
) -> Page[AdjustmentListItem]:
    rows, total = await adjustments.list_adjustments(
        session,
        ctx,
        page=page,
        search=q,
        filters={
            "status": status,
            "site_id": site_id,
            "warehouse_id": warehouse_id,
            "reason_code": reason_code,
        },
    )
    names = await _names(
        session,
        ctx,
        material_ids=set(),
        unit_ids=set(),
        warehouse_ids={r.warehouse_id for r in rows},
        site_ids={r.site_id for r in rows},
    )
    items = []
    for r in rows:
        item = AdjustmentListItem.model_validate(r)
        item.warehouse_code = names.warehouse_code(r.warehouse_id)
        item.site_code = names.site_code(r.site_id)
        items.append(item)
    return Page.of(items, params=page, total=total)


@adjustment_router.get(
    "/{adjustment_id}",
    response_model=AdjustmentRead,
    dependencies=[require(adjustments.PERM_VIEW)],
)
async def get_adjustment(adjustment_id: UUID, ctx: Access, session: SessionDep) -> AdjustmentRead:
    return await _adjustment_view(session, ctx, await adjustments.get(session, ctx, adjustment_id))


@adjustment_router.post(
    "",
    response_model=AdjustmentRead,
    status_code=201,
    dependencies=[require(adjustments.PERM_ADJUST)],
    summary="Raise an adjustment as a draft; it moves nothing until it is approved",
)
async def create_adjustment(payload: AdjustmentCreate, ctx: Access, uow: UowDep) -> AdjustmentRead:
    adjustment = await adjustments.create(uow.session, ctx, payload)
    return await _adjustment_view(uow.session, ctx, adjustment)


@adjustment_router.put(
    "/{adjustment_id}",
    response_model=AdjustmentRead,
    dependencies=[require(adjustments.PERM_ADJUST)],
    summary="Edit a draft or rejected adjustment",
)
async def update_adjustment(
    adjustment_id: UUID,
    payload: AdjustmentCreate,
    ctx: Access,
    uow: UowDep,
    if_match: IfMatch = None,
) -> AdjustmentRead:
    adjustment = await adjustments.update(
        uow.session, ctx, adjustment_id, payload, expected_version=if_match
    )
    return await _adjustment_view(uow.session, ctx, adjustment)


@adjustment_router.post(
    "/{adjustment_id}/submit",
    response_model=AdjustmentRead,
    dependencies=[require(adjustments.PERM_ADJUST)],
    summary="Send for approval. A rule may approve a small one at once, which posts it.",
)
async def submit_adjustment(
    adjustment_id: UUID, ctx: Access, uow: UowDep, if_match: IfMatch = None
) -> AdjustmentRead:
    adjustment = await adjustments.submit(
        uow.session, ctx, adjustment_id, expected_version=if_match
    )
    return await _adjustment_view(uow.session, ctx, adjustment)


@adjustment_router.post(
    "/{adjustment_id}/withdraw",
    response_model=AdjustmentRead,
    dependencies=[require(adjustments.PERM_ADJUST)],
    summary="Take a pending adjustment back to a draft (before any step is approved)",
)
async def withdraw_adjustment(
    adjustment_id: UUID, ctx: Access, uow: UowDep, payload: CancelBody | None = None
) -> AdjustmentRead:
    adjustment = await adjustments.withdraw(
        uow.session, ctx, adjustment_id, payload.reason if payload else None
    )
    return await _adjustment_view(uow.session, ctx, adjustment)


@adjustment_router.post(
    "/{adjustment_id}/cancel",
    response_model=AdjustmentRead,
    dependencies=[require(adjustments.PERM_ADJUST)],
    summary="Cancel a draft or rejected adjustment",
)
async def cancel_adjustment(
    adjustment_id: UUID, payload: CancelBody, ctx: Access, uow: UowDep
) -> AdjustmentRead:
    adjustment = await adjustments.cancel(uow.session, ctx, adjustment_id, payload.reason)
    return await _adjustment_view(uow.session, ctx, adjustment)


_ = Any
