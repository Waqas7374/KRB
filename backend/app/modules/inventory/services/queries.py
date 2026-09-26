"""Reading stock: balances, the ledger, and what is running low."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.core.pagination import PageParams, apply_sort
from app.modules.inventory.models import InventoryBalance, InventoryTransaction
from app.modules.masterdata.services import material_lookup
from app.modules.rules.domain.enums import RuleType
from app.modules.rules.services import resolver as rule_resolver

PERM_VIEW = "inventory.view"


def balance_repository(session: AsyncSession) -> ScopedRepository[InventoryBalance]:
    return ScopedRepository(
        session,
        InventoryBalance,
        entity_name="Stock balance",
        sortable={"quantity_on_hand", "total_value", "last_movement_at", "created_at"},
        searchable=(),
        default_sort="-last_movement_at",
    )


async def list_balances(
    session: AsyncSession,
    ctx: AccessContext,
    *,
    page: PageParams,
    filters: dict[str, Any],
    in_stock_only: bool = False,
) -> tuple[list[InventoryBalance], int]:
    repo = balance_repository(session)
    stmt = repo.apply_filters(repo.base_query(ctx, PERM_VIEW), filters)
    if in_stock_only:
        stmt = stmt.where(InventoryBalance.quantity_on_hand > 0)
    total = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ) or 0
    stmt = apply_sort(
        stmt, InventoryBalance, page.sort, allowed=repo.sortable, default=repo.default_sort
    )
    rows = (
        (await session.execute(stmt.limit(page.limit).offset(page.offset))).scalars().unique().all()
    )
    return list(rows), total


async def list_ledger(
    session: AsyncSession, ctx: AccessContext, *, page: PageParams, filters: dict[str, Any]
) -> tuple[list[InventoryTransaction], int]:
    repo: ScopedRepository[InventoryTransaction] = ScopedRepository(
        session,
        InventoryTransaction,
        entity_name="Stock movement",
        sortable={"posted_at", "transaction_date", "created_at"},
        searchable=(),
        default_sort="-posted_at",
    )
    stmt = repo.apply_filters(repo.base_query(ctx, PERM_VIEW), filters)
    total = (
        await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ) or 0
    stmt = apply_sort(
        stmt, InventoryTransaction, page.sort, allowed=repo.sortable, default=repo.default_sort
    )
    rows = (
        (await session.execute(stmt.limit(page.limit).offset(page.offset))).scalars().unique().all()
    )
    return list(rows), total


async def _level_for(
    session: AsyncSession,
    ctx: AccessContext,
    row: InventoryBalance,
    materials: dict[UUID, material_lookup.MaterialInfo],
) -> Decimal | None:
    """The reorder level that applies to this stock: a rule scoped by material
    and site wins over the material's own level."""
    material = materials.get(row.material_id)
    rule = await rule_resolver.resolve(
        session,
        company_id=ctx.company_id,
        rule_type=RuleType.REORDER_LEVEL,
        context={
            "material_id": row.material_id,
            "material_category_id": material.category_id if material else None,
            "site_id": row.site_id,
            "project_id": row.project_id,
        },
    )
    if rule is not None:
        return Decimal(str(rule.value["min_quantity"]))
    return material.reorder_level if material else None


async def low_stock_levels(
    session: AsyncSession, ctx: AccessContext, rows: list[InventoryBalance]
) -> dict[UUID, Decimal]:
    """balance id -> reorder level, for the rows at or below it."""
    materials = await material_lookup.materials(
        session, company_id=ctx.company_id, material_ids={r.material_id for r in rows}
    )
    low: dict[UUID, Decimal] = {}
    for row in rows:
        level = await _level_for(session, ctx, row, materials)
        if level is not None and level > 0 and row.quantity_on_hand <= level:
            low[row.id] = level
    return low


async def low_stock(
    session: AsyncSession, ctx: AccessContext
) -> tuple[list[InventoryBalance], dict[UUID, Decimal]]:
    repo = balance_repository(session)
    rows = list(
        (await session.execute(repo.base_query(ctx, PERM_VIEW).limit(2000)))
        .scalars()
        .unique()
        .all()
    )
    low = await low_stock_levels(session, ctx, rows)
    return [r for r in rows if r.id in low], low
