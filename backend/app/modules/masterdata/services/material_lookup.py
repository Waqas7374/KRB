"""Material facts other modules need to validate document lines, exposed
without the ORM models (module boundary: tests/unit/test_architecture.py)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.masterdata.models import Material, MaterialUnit, Unit


@dataclass(frozen=True, slots=True)
class MaterialInfo:
    id: UUID
    sku: str
    name: str
    base_unit_id: UUID
    # The base unit plus every alternate unit configured for the material.
    unit_ids: frozenset[UUID]
    is_purchasable: bool
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
            unit_ids=frozenset({m.base_unit_id} | by_material.get(m.id, set())),
            is_purchasable=m.is_purchasable,
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
