"""Inventory endpoints: balances, the ledger, low stock."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access, PageDep, SessionDep, require
from app.core.access import AccessContext
from app.core.pagination import Page
from app.modules.inventory.models import InventoryBalance, InventoryTransaction
from app.modules.inventory.services import queries
from app.modules.masterdata.services import material_lookup, warehouse_lookup
from app.modules.org.services import document_lookup

router = APIRouter(prefix="/inventory", tags=["inventory"])

PERM_VALUATION = "inventory.view_valuation"


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BalanceRead(ApiModel):
    id: UUID
    warehouse_id: UUID
    warehouse_code: str | None = None
    warehouse_name: str | None = None
    site_id: UUID | None
    site_code: str | None = None
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    unit_code: str | None = None
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_in_transit: Decimal
    # None for a reader without inventory.view_valuation.
    average_cost: Decimal | None = None
    total_value: Decimal | None = None
    last_movement_at: datetime | None = None
    reorder_level: Decimal | None = None
    is_low: bool = False


class LedgerRead(ApiModel):
    id: UUID
    posted_at: datetime
    transaction_date: date
    warehouse_id: UUID
    warehouse_code: str | None = None
    material_id: UUID
    material_sku: str | None = None
    material_name: str | None = None
    unit_code: str | None = None
    txn_type: str
    quantity_in: Decimal
    quantity_out: Decimal
    unit_cost: Decimal | None = None
    value_in: Decimal | None = None
    value_out: Decimal | None = None
    balance_quantity_after: Decimal
    balance_value_after: Decimal | None = None
    source_type: str
    source_id: UUID
    reversal_of_id: UUID | None
    remarks: str | None


async def _balance_views(
    session: AsyncSession,
    ctx: AccessContext,
    rows: list[InventoryBalance],
    low: dict[UUID, Decimal],
) -> list[BalanceRead]:
    company = ctx.company_id
    materials = await material_lookup.materials(
        session, company_id=company, material_ids={r.material_id for r in rows}
    )
    units = await material_lookup.unit_codes(
        session, company_id=company, unit_ids={m.base_unit_id for m in materials.values()}
    )
    whs = await warehouse_lookup.names(
        session, company_id=company, warehouse_ids={r.warehouse_id for r in rows}
    )
    codes = await document_lookup.codes(
        session,
        company_id=company,
        project_ids=set(),
        site_ids={r.site_id for r in rows if r.site_id},
    )
    hidden = not ctx.has(PERM_VALUATION)
    out = []
    for r in rows:
        view = BalanceRead.model_validate(r)
        m = materials.get(r.material_id)
        view.material_sku = m.sku if m else None
        view.material_name = m.name if m else None
        view.unit_code = units.get(m.base_unit_id) if m else None
        view.warehouse_code, view.warehouse_name = whs.get(r.warehouse_id, (None, None))
        if r.site_id:
            view.site_code = codes.get(r.site_id, (None, None))[0]
        if hidden:
            view.average_cost = view.total_value = None
        level = low.get(r.id)
        if level is not None:
            view.reorder_level = level
            view.is_low = True
        out.append(view)
    return out


@router.get(
    "/balances", response_model=Page[BalanceRead], dependencies=[require(queries.PERM_VIEW)]
)
async def balances(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    warehouse_id: UUID | None = None,
    material_id: UUID | None = None,
    site_id: UUID | None = None,
    in_stock: bool = False,
) -> Page[BalanceRead]:
    rows, total = await queries.list_balances(
        session,
        ctx,
        page=page,
        filters={"warehouse_id": warehouse_id, "material_id": material_id, "site_id": site_id},
        in_stock_only=in_stock,
    )
    low = await queries.low_stock_levels(session, ctx, rows)
    return Page.of(await _balance_views(session, ctx, rows, low), params=page, total=total)


@router.get(
    "/low-stock", response_model=list[BalanceRead], dependencies=[require(queries.PERM_VIEW)]
)
async def low_stock(ctx: Access, session: SessionDep) -> list[BalanceRead]:
    """Stock at or below its reorder level (a business rule scoped by material
    and site, else the material's own level)."""
    rows, low = await queries.low_stock(session, ctx)
    return await _balance_views(session, ctx, rows, low)


@router.get("/ledger", response_model=Page[LedgerRead], dependencies=[require(queries.PERM_VIEW)])
async def ledger(
    ctx: Access,
    session: SessionDep,
    page: PageDep,
    warehouse_id: UUID | None = None,
    material_id: UUID | None = None,
    source_id: UUID | None = None,
    txn_type: Annotated[list[str] | None, Query()] = None,
) -> Page[LedgerRead]:
    rows, total = await queries.list_ledger(
        session,
        ctx,
        page=page,
        filters={
            "warehouse_id": warehouse_id,
            "material_id": material_id,
            "source_id": source_id,
            "txn_type": txn_type,
        },
    )
    company = ctx.company_id
    materials = await material_lookup.materials(
        session, company_id=company, material_ids={r.material_id for r in rows}
    )
    units = await material_lookup.unit_codes(
        session, company_id=company, unit_ids={r.unit_id for r in rows}
    )
    whs = await warehouse_lookup.names(
        session, company_id=company, warehouse_ids={r.warehouse_id for r in rows}
    )
    hidden = not ctx.has(PERM_VALUATION)
    items = []
    for r in rows:
        view = LedgerRead.model_validate(r)
        m = materials.get(r.material_id)
        view.material_sku = m.sku if m else None
        view.material_name = m.name if m else None
        view.unit_code = units.get(r.unit_id)
        view.warehouse_code = whs.get(r.warehouse_id, (None, None))[0]
        if hidden:
            view.unit_cost = view.value_in = view.value_out = view.balance_value_after = None
        items.append(view)
    return Page.of(items, params=page, total=total)


_ = (select, InventoryTransaction)
