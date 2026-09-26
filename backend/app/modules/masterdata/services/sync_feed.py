"""Reference data a phone caches: materials, units, truck types (docs/06 §4)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sync import FeedRow
from app.modules.masterdata.models import Material, MaterialUnit, TruckType, Unit


async def changes(
    session: AsyncSession, company_id: UUID, entity: str, *, since: int, limit: int
) -> list[FeedRow]:
    company = company_id
    if entity == "materials":
        rows = (
            (
                await session.execute(
                    select(Material)
                    .where(
                        Material.company_id == company,
                        Material.server_seq.is_not(None),
                        Material.server_seq > since,
                    )
                    .order_by(Material.server_seq)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        alternates: dict[Any, set[Any]] = {}
        if rows:
            found = await session.execute(
                select(MaterialUnit.material_id, MaterialUnit.unit_id).where(
                    MaterialUnit.material_id.in_([r.id for r in rows]),
                    MaterialUnit.deleted_at.is_(None),
                )
            )
            for material_id, unit_id in found.tuples():
                alternates.setdefault(material_id, set()).add(unit_id)
        return [
            FeedRow(
                "materials",
                r.id,
                int(r.server_seq or 0),
                deleted=r.deleted_at is not None,
                data={
                    "sku": r.sku,
                    "name": r.name,
                    "base_unit_id": str(r.base_unit_id),
                    "category_id": str(r.category_id) if r.category_id else None,
                    "unit_ids": sorted(
                        str(u) for u in ({r.base_unit_id} | alternates.get(r.id, set()))
                    ),
                    "is_purchasable": r.is_purchasable,
                    "is_stockable": r.is_stockable,
                },
            )
            for r in rows
        ]
    if entity == "units":
        units = (
            (
                await session.execute(
                    select(Unit)
                    .where(
                        Unit.company_id == company,
                        Unit.server_seq.is_not(None),
                        Unit.server_seq > since,
                    )
                    .order_by(Unit.server_seq)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            FeedRow(
                "units",
                u.id,
                int(u.server_seq or 0),
                deleted=u.deleted_at is not None,
                data={
                    "code": u.code,
                    "name": u.name,
                    "symbol": u.symbol,
                    "dimension": u.dimension,
                    "precision": u.precision,
                },
            )
            for u in units
        ]
    trucks = (
        (
            await session.execute(
                select(TruckType)
                .where(
                    TruckType.company_id == company,
                    TruckType.server_seq.is_not(None),
                    TruckType.server_seq > since,
                )
                .order_by(TruckType.server_seq)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        FeedRow(
            "truck_types",
            t.id,
            int(t.server_seq or 0),
            deleted=t.deleted_at is not None,
            data={
                "code": t.code,
                "name": t.name,
                "default_max_tonnage": str(t.default_max_tonnage),
                "typical_volume_cft": str(t.typical_volume_cft) if t.typical_volume_cft else None,
            },
        )
        for t in trucks
    ]
