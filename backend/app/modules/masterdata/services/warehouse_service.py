"""Truck types and warehouses."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.crud import ScopedRepository
from app.modules.masterdata.models import TruckType, Warehouse


def truck_type_repository(session: AsyncSession) -> ScopedRepository[TruckType]:
    return ScopedRepository(
        session,
        TruckType,
        entity_name="Truck type",
        sortable={"code", "name", "default_max_tonnage", "created_at"},
        searchable=("code", "name"),
        default_sort="code",
    )


def warehouse_repository(session: AsyncSession) -> ScopedRepository[Warehouse]:
    return ScopedRepository(
        session,
        Warehouse,
        entity_name="Warehouse",
        sortable={"code", "name", "warehouse_type", "created_at"},
        searchable=("code", "name"),
        default_sort="code",
    )


async def create_truck_type(
    session: AsyncSession, ctx: AccessContext, *, payload: dict[str, Any]
) -> TruckType:
    repo = truck_type_repository(session)
    await repo.assert_code_available(ctx.company_id, "code", payload["code"])
    return await repo.create(company_id=ctx.company_id, **payload)


async def create_warehouse(
    session: AsyncSession, ctx: AccessContext, *, payload: dict[str, Any]
) -> Warehouse:
    repo = warehouse_repository(session)
    await repo.assert_code_available(ctx.company_id, "code", payload["code"])
    return await repo.create(company_id=ctx.company_id, **payload)
