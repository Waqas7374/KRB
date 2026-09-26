"""Warehouse facts other modules need, exposed without the ORM models (module
boundary: tests/unit/test_architecture.py)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.masterdata.models import Warehouse
from app.modules.org.services import site_lookup


@dataclass(frozen=True, slots=True)
class WarehouseInfo:
    id: UUID
    code: str
    name: str
    site_id: UUID
    project_id: UUID | None
    is_default_receiving: bool
    is_active: bool


async def _info(session: AsyncSession, row: Warehouse) -> WarehouseInfo:
    projects = await site_lookup.project_ids_for_sites(session, [row.site_id])
    return WarehouseInfo(
        id=row.id,
        code=row.code,
        name=row.name,
        site_id=row.site_id,
        project_id=projects.get(row.site_id),
        is_default_receiving=row.is_default_receiving,
        is_active=row.deleted_at is None,
    )


async def get(
    session: AsyncSession, *, company_id: UUID, warehouse_id: UUID
) -> WarehouseInfo | None:
    row = (
        await session.execute(
            select(Warehouse).where(
                Warehouse.company_id == company_id, Warehouse.id == warehouse_id
            )
        )
    ).scalar_one_or_none()
    return await _info(session, row) if row is not None and row.deleted_at is None else None


async def default_receiving(
    session: AsyncSession, *, company_id: UUID, site_id: UUID
) -> WarehouseInfo | None:
    """The site's default receiving warehouse, or its only warehouse if it has
    just one; None when the choice is not obvious."""
    rows = (
        (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.company_id == company_id,
                    Warehouse.site_id == site_id,
                    Warehouse.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    chosen = next((w for w in rows if w.is_default_receiving), None)
    if chosen is None and len(rows) == 1:
        chosen = rows[0]
    return await _info(session, chosen) if chosen is not None else None


async def names(
    session: AsyncSession, *, company_id: UUID, warehouse_ids: set[UUID]
) -> dict[UUID, tuple[str, str]]:
    """id -> (code, name)."""
    if not warehouse_ids:
        return {}
    rows = await session.execute(
        select(Warehouse.id, Warehouse.code, Warehouse.name).where(
            Warehouse.company_id == company_id, Warehouse.id.in_(list(warehouse_ids))
        )
    )
    return {i: (c, n) for i, c, n in rows.tuples().all()}


@dataclass(frozen=True, slots=True)
class WarehouseOption:
    id: UUID
    code: str
    name: str
    site_id: UUID
    is_default_receiving: bool


async def options(
    session: AsyncSession, *, company_id: UUID, site_ids: set[UUID] | None
) -> list[WarehouseOption]:
    """Live warehouses, all of them or only those at `site_ids`, for a picker."""
    stmt = select(Warehouse).where(
        Warehouse.company_id == company_id, Warehouse.deleted_at.is_(None)
    )
    if site_ids is not None:
        if not site_ids:
            return []
        stmt = stmt.where(Warehouse.site_id.in_(site_ids))
    rows = (await session.execute(stmt.order_by(Warehouse.code))).scalars().all()
    return [
        WarehouseOption(
            id=w.id,
            code=w.code,
            name=w.name,
            site_id=w.site_id,
            is_default_receiving=w.is_default_receiving,
        )
        for w in rows
    ]
