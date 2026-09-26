"""Material facts other modules need to validate document lines, exposed
without the ORM models (module boundary: tests/unit/test_architecture.py)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.masterdata.models import Material, MaterialUnit, TruckType, Unit


@dataclass(frozen=True, slots=True)
class MaterialInfo:
    id: UUID
    sku: str
    name: str
    base_unit_id: UUID
    category_id: UUID | None
    # The base unit plus every alternate unit configured for the material.
    unit_ids: frozenset[UUID]
    is_purchasable: bool
    is_stockable: bool
    reorder_level: Decimal | None
    is_active: bool


async def materials(
    session: AsyncSession, *, company_id: UUID, material_ids: set[UUID]
) -> dict[UUID, MaterialInfo]:
    if not material_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Material).where(
                    Material.company_id == company_id, Material.id.in_(list(material_ids))
                )
            )
        )
        .scalars()
        .all()
    )
    alternates = (
        await session.execute(
            select(MaterialUnit.material_id, MaterialUnit.unit_id).where(
                MaterialUnit.material_id.in_(list(material_ids)),
                MaterialUnit.deleted_at.is_(None),
            )
        )
    ).tuples()
    by_material: dict[UUID, set[UUID]] = {}
    for material_id, unit_id in alternates.all():
        by_material.setdefault(material_id, set()).add(unit_id)

    return {
        m.id: MaterialInfo(
            id=m.id,
            sku=m.sku,
            name=m.name,
            base_unit_id=m.base_unit_id,
            category_id=m.category_id,
            unit_ids=frozenset({m.base_unit_id} | by_material.get(m.id, set())),
            is_purchasable=m.is_purchasable,
            is_stockable=m.is_stockable,
            reorder_level=m.reorder_level,
            is_active=m.deleted_at is None,
        )
        for m in rows
    }


async def unit_codes(
    session: AsyncSession, *, company_id: UUID, unit_ids: set[UUID]
) -> dict[UUID, str]:
    if not unit_ids:
        return {}
    rows = (
        await session.execute(
            select(Unit.id, Unit.code).where(
                Unit.company_id == company_id, Unit.id.in_(list(unit_ids))
            )
        )
    ).tuples()
    return dict(rows.all())


@dataclass(frozen=True, slots=True)
class TruckTypeInfo:
    id: UUID
    code: str
    name: str
    # The standing tonnage limit when no business rule says otherwise.
    default_max_tonnage: Decimal


async def truck_type(
    session: AsyncSession, *, company_id: UUID, truck_type_id: UUID
) -> TruckTypeInfo | None:
    row = (
        await session.execute(
            select(TruckType).where(
                TruckType.company_id == company_id,
                TruckType.id == truck_type_id,
                TruckType.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return TruckTypeInfo(
        id=row.id, code=row.code, name=row.name, default_max_tonnage=row.default_max_tonnage
    )


async def truck_type_names(
    session: AsyncSession, *, company_id: UUID, truck_type_ids: set[UUID]
) -> dict[UUID, str]:
    if not truck_type_ids:
        return {}
    rows = await session.execute(
        select(TruckType.id, TruckType.name).where(
            TruckType.company_id == company_id, TruckType.id.in_(list(truck_type_ids))
        )
    )
    return dict(rows.tuples().all())


async def unit_by_code(session: AsyncSession, *, company_id: UUID, code: str) -> UUID | None:
    found: UUID | None = await session.scalar(
        select(Unit.id).where(Unit.company_id == company_id, Unit.code == code)
    )
    return found
